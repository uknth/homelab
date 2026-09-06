"""The original (and, for now, only in-process) research engine: the exact
plan -> search -> fetch -> summarise -> synthesise loop that used to live
directly in pipeline.run_job. Moved here unchanged so a sidecar engine can
sit next to it behind the same `ResearchEngine` contract (engines/base.py)
-- see docs/spec/research.md for the pipeline stages this loop implements.

`on_stage`/`check_cancelled` replace the direct `_set_stage` /
`db.start_stage` / `db.finish_stage` / `_check_cancelled` calls that used
to be sprinkled through pipeline.run_job: this engine still calls them at
exactly the same points, just through the callables pipeline.py hands it
(see pipeline.py's `_make_on_stage` for what a call actually does to the
db and the SSE stream).

Everything that is NOT part of "produce notes from a topic" -- resolving a
seed URL into a topic, deciding the job's slug, writing the tree to disk,
the `ready` transition -- stays in pipeline.py, because those apply to
every engine identically, remote ones included.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from .. import db, excerpts, fetch, llm, search
from ..depth import DepthConfig, get_depth_config
from ..models import ExcerptRecord, FetchedDoc, SearchResult
from ..pipeline import PipelineError
from ..write import NoteSpec, SourceCitation
from .base import EngineResult

if TYPE_CHECKING:
    from ..pipeline import Pipeline

logger = logging.getLogger("researchd.engines.native")


async def _fetch_and_summarise(
    pipeline: "Pipeline", job_id: str, subtopic_title: str, results: list[SearchResult],
    check_cancelled: Callable[[], None],
) -> list[dict]:
    """Add each search result as a source row, fetch it, summarise it, and
    verify its excerpts. Returns the summaries that made it all the way
    through; a failure at any step for a given source is recorded on that
    source's row and the loop continues -- one dead source never fails the
    job (docs/spec/research.md).
    """
    summaries: list[dict] = []
    for r in results:
        check_cancelled()
        source_id = db.add_source(
            pipeline.conn, job_id, subtopic_title, r.url, doi=r.doi, title=r.title,
        )

        doc: FetchedDoc | None = await fetch.fetch_document(
            pipeline.http_client, pipeline.settings, r.url,
        )
        if doc is None:
            db.update_source_status(pipeline.conn, source_id, "failed", error="fetch failed")
            continue
        db.update_source_status(pipeline.conn, source_id, "fetched", title=doc.title)
        pipeline.events.publish(
            job_id, {"type": "source_fetched", "subtopic": subtopic_title, "url": doc.url},
        )

        result, _chunk = await llm.summarise(
            pipeline.llm_client, settings=pipeline.settings,
            title=doc.title, url=doc.url, doc_text=doc.text,
        )
        if result is None:
            db.update_source_status(pipeline.conn, source_id, "failed", error="summarise failed")
            continue

        verified: list[ExcerptRecord] = []
        for ex in result.excerpts:
            text = excerpts.extract_and_verify(doc.text, ex.start, ex.end)
            if text is None:
                continue  # dropped silently -- see excerpts.py
            db.add_excerpt(pipeline.conn, source_id, ex.start, ex.end, text, ex.note)
            verified.append(ExcerptRecord(start=ex.start, end=ex.end, text=text, note=ex.note))

        db.update_source_status(pipeline.conn, source_id, "summarised")
        summaries.append({
            "source_id": source_id, "url": doc.url, "title": doc.title,
            "summary": result.summary, "excerpts": verified,
        })
        pipeline.events.publish(
            job_id, {"type": "source_summarised", "subtopic": subtopic_title, "url": doc.url},
        )
    return summaries


async def _run_recursion(
    pipeline: "Pipeline", job_id: str, depth_cfg: DepthConfig,
    seed_related_topics: list[str], citation_index: int,
    check_cancelled: Callable[[], None],
) -> tuple[list[NoteSpec], list[SourceCitation], int]:
    """Depth 4/5 only. Every related-topic string that reaches this
    function came out of models.SynthesiseOutcome.related_topics, which is
    schema-validated as a plain list of strings (models.py) -- it is used
    here exclusively as a search-query string, exactly like a plan-stage
    query. There is no code path from this string to a URL, a filesystem
    path, or a shell.
    """
    note_specs: list[NoteSpec] = []
    all_citations: list[SourceCitation] = []
    pending = seed_related_topics[: depth_cfg.related_topic_budget]

    for level in range(depth_cfg.recursion_levels):
        if not pending:
            break
        next_pending: list[str] = []
        for query in pending:
            check_cancelled()
            results = await search.gather_sources(
                pipeline.http_client, pipeline.settings, [query],
                limit=depth_cfg.sources_per_subtopic,
                prioritise_papers=depth_cfg.prioritise_papers,
            )
            summaries = await _fetch_and_summarise(pipeline, job_id, query, results, check_cancelled)
            if not summaries:
                continue

            allow_related = level < depth_cfg.recursion_levels - 1
            outcome = await llm.synthesise(
                pipeline.llm_client, settings=pipeline.settings,
                subtopic_title=query, scope="a related topic surfaced during synthesis",
                summaries=summaries, allow_related=allow_related,
                related_budget=depth_cfg.related_topic_budget, allow_open_questions=False,
            )

            citations = []
            for s in summaries:
                citation = SourceCitation(
                    index=citation_index, title=s["title"], url=s["url"], excerpts=s["excerpts"],
                )
                citations.append(citation)
                all_citations.append(citation)
                citation_index += 1

            note_specs.append(NoteSpec(subtopic_title=query, body=outcome.body, sources=citations))
            next_pending.extend(outcome.related_topics)
            pipeline.events.publish(job_id, {"type": "note_ready", "subtopic": query, "recursed": True})

        pending = next_pending[: depth_cfg.related_topic_budget]

    return note_specs, all_citations, citation_index


class NativeEngine:
    """The built-in engine: everything that ran directly inside
    pipeline.run_job before the engine seam existed. Needs live db/http/llm
    access, so it is constructed with the whole Pipeline rather than a
    narrower slice of it -- there is no meaningful "native engine" that
    does not share the process's single resident-model client and db
    connection.
    """

    id = "native"
    label = "Built-in"

    def __init__(self, pipeline: "Pipeline") -> None:
        self.pipeline = pipeline

    async def healthy(self) -> bool:
        return True

    async def run(
        self, *, job_id: str, topic: str, depth: int,
        on_stage: Callable[[str, str], Awaitable[None]],
        check_cancelled: Callable[[], None],
    ) -> EngineResult:
        pipeline = self.pipeline
        conn = pipeline.conn
        depth_cfg = get_depth_config(depth)

        # ---- planning ------------------------------------------------
        outline = await llm.plan(
            pipeline.llm_client, settings=pipeline.settings,
            topic=topic, n_subtopics=depth_cfg.subtopics,
        )
        if outline is None:
            raise PipelineError("planning failed: the model did not return a usable outline")
        await on_stage("planning", f"{len(outline.subtopics)} subtopics")
        check_cancelled()

        # ---- searching -------------------------------------------------
        await on_stage("searching", "")
        subtopic_results: dict[str, list[SearchResult]] = {}
        for st in outline.subtopics:
            check_cancelled()
            queries = st.search_queries or [st.title]
            results = await search.gather_sources(
                pipeline.http_client, pipeline.settings, queries,
                limit=depth_cfg.sources_per_subtopic,
                prioritise_papers=depth_cfg.prioritise_papers,
            )
            subtopic_results[st.title] = results
            pipeline.events.publish(
                job_id, {"type": "sources_found", "subtopic": st.title, "count": len(results)},
            )
        total_sources = sum(len(v) for v in subtopic_results.values())
        await on_stage("searching", f"{total_sources} sources")
        check_cancelled()

        # ---- fetching + summarising, per subtopic -----------------------
        # Fetching and summarising are two distinct job states, but they
        # share one per-source loop (via _fetch_and_summarise) so a source
        # never gets fetched in one pass and summarised in a second pass
        # against a possibly-stale copy of its text.
        await on_stage("fetching", "")
        await on_stage("summarising", "")

        summaries_by_subtopic: dict[str, list[dict]] = {}
        n_summarised = 0
        for st in outline.subtopics:
            check_cancelled()
            summaries = await _fetch_and_summarise(
                pipeline, job_id, st.title, subtopic_results.get(st.title, []), check_cancelled,
            )
            summaries_by_subtopic[st.title] = summaries
            n_summarised += len(summaries)
            pipeline.events.publish(
                job_id, {"type": "progress", "stage": "summarising",
                         "done": n_summarised, "total": total_sources},
            )

        await on_stage("fetching", f"{total_sources} sources")
        await on_stage("summarising", f"{n_summarised} summarised")
        check_cancelled()

        # ---- synthesising --------------------------------------------
        await on_stage("synthesising", "")
        note_specs: list[NoteSpec] = []
        all_citations: list[SourceCitation] = []
        all_related: list[str] = []
        all_open_questions: list[str] = []
        citation_index = 1

        for st in outline.subtopics:
            check_cancelled()
            summaries = summaries_by_subtopic.get(st.title, [])
            if not summaries:
                continue  # every source for this subtopic failed; skip it, do not fail the job

            outcome = await llm.synthesise(
                pipeline.llm_client, settings=pipeline.settings,
                subtopic_title=st.title, scope=st.scope, summaries=summaries,
                allow_related=depth_cfg.related_topics_note,
                related_budget=depth_cfg.related_topic_budget,
                allow_open_questions=depth_cfg.open_questions_note,
            )

            citations = []
            for s in summaries:
                citation = SourceCitation(
                    index=citation_index, title=s["title"], url=s["url"], excerpts=s["excerpts"],
                )
                citations.append(citation)
                all_citations.append(citation)
                citation_index += 1

            note_specs.append(NoteSpec(subtopic_title=st.title, body=outcome.body, sources=citations))
            all_related.extend(outcome.related_topics)
            all_open_questions.extend(outcome.open_questions)
            pipeline.events.publish(job_id, {"type": "note_ready", "subtopic": st.title})

        if not note_specs:
            raise PipelineError("every subtopic failed to produce sources; nothing to write")

        if depth_cfg.recursion_levels > 0 and all_related:
            recursed_notes, recursed_citations, citation_index = await _run_recursion(
                pipeline, job_id, depth_cfg, all_related, citation_index, check_cancelled,
            )
            note_specs.extend(recursed_notes)
            all_citations.extend(recursed_citations)

        await on_stage("synthesising", f"{len(note_specs)} notes")
        check_cancelled()

        return EngineResult(
            title=outline.title, notes=note_specs, all_sources=all_citations,
            related_topics=all_related, open_questions=all_open_questions,
        )
