"""Stage orchestration: the fixed pipeline that turns one job into a
markdown tree.

`queued -> planning -> searching -> fetching -> summarising ->
synthesising -> writing -> ready`, plus `failed` / `cancelled`.
`published` is set only by the external callback endpoint (main.py),
never by anything in this module.

Concurrency is 1: a single asyncio worker task pulls job ids off an
in-process queue and runs them one at a time, because there is exactly
one resident model on ai01 and this pipeline is the only thing allowed to
call it. The queue is backed by SQLite (`db.list_active_jobs`), so a
container restart resumes cleanly -- see `Pipeline.start`.

This module is also where the one place model output is allowed to
influence *what gets fetched next* lives: at depth 4/5, `synthesise`
proposes related topics, and `_run_recursion` below treats every one of
them strictly as a search-query string passed to `search.gather_sources`
-- never as a URL, never as anything resembling a path, a command, or a
credential. See docs/spec/research.md, "The model never chooses an
action".
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from . import db, excerpts, fetch, llm, search, write
from .config import Settings
from .depth import DepthConfig, get_depth_config
from .models import ExcerptRecord, FetchedDoc, SearchResult
from .write import NoteSpec, SourceCitation

logger = logging.getLogger("researchd.pipeline")


class PipelineError(Exception):
    """A stage failed in a way the job cannot recover from (as opposed to
    a single dead source, which is recorded and skipped, never raised)."""


class JobCancelled(Exception):
    """Raised internally to unwind out of a job once cancellation has been
    observed; never surfaces as a `failed` status."""


class EventBus:
    """A tiny in-process pub/sub for SSE. One `asyncio.Queue` per active
    subscriber of a job id; publishing fans an event dict out to every
    subscriber currently watching that job. Nothing here is persisted --
    `db.list_stages` / `db.list_sources` are the durable record a late
    subscriber (or a fresh `GET /api/jobs/{id}`) reads instead.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(job_id)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: dict) -> None:
        for q in self._subscribers.get(job_id, []):
            q.put_nowait(event)


class Pipeline:
    """Owns the DB connection, the two shared HTTP clients (one for omlx,
    one for search + fetch), the event bus, and the single worker task.
    One instance lives for the lifetime of the process (see main.py).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.conn = db.connect(settings.db_path)
        self.llm_client = llm.make_client(settings)
        # The User-Agent belongs on the client, not on individual calls: the
        # search backends were being queried with httpx's default UA and
        # Wikipedia's API answered 403 to every single query (verified live
        # 2026-09-04 — ten consecutive "403 Forbidden" on w/api.php). Their
        # policy requires an identifying UA with contact info, and several
        # other sources rate-limit anonymous clients the same way.
        #
        # Setting it here covers search AND fetch, so there is no second place
        # for it to be forgotten. fetch.py still passes it explicitly, which is
        # harmless — an explicit header simply overrides this default with the
        # same value.
        self.http_client = httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": settings.user_agent},
        )
        self.events = EventBus()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Recover from a restart: anything that was `queued` never
        started, so it is safe to just re-enqueue it. Anything mid-flight
        (planning..writing) was interrupted mid-stage with no safe resume
        point, so it is marked `failed` rather than silently retried.
        """
        Path(self.settings.tree_dir).mkdir(parents=True, exist_ok=True)
        for job in db.list_active_jobs(self.conn):
            if job["status"] == "queued":
                self.queue.put_nowait(job["id"])
            else:
                db.update_job_status(
                    self.conn, job["id"], "failed",
                    error="interrupted by a service restart",
                )
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        await self.llm_client.aclose()
        await self.http_client.aclose()

    def submit(self, *, topic: str | None, url: str | None, depth: int) -> str:
        job_id = db.new_job_id()
        label = topic or url or "untitled"
        slug = write.slugify(label)
        db.create_job(
            self.conn, job_id=job_id, topic=label, source_url=url,
            depth=depth, slug=slug, status="queued",
        )
        self.queue.put_nowait(job_id)
        return job_id

    def cancel(self, job_id: str) -> bool:
        job = db.get_job(self.conn, job_id)
        if job is None or job["status"] in ("ready", "published", "failed", "cancelled"):
            return False
        db.update_job_status(self.conn, job_id, "cancelled")
        self.events.publish(job_id, {"type": "stage", "stage": "cancelled"})
        return True

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await run_job(self, job_id)
            except Exception:
                logger.exception("job %s crashed outside its own error handling", job_id)
            finally:
                self.queue.task_done()


def _is_cancelled(pipeline: Pipeline, job_id: str) -> bool:
    job = db.get_job(pipeline.conn, job_id)
    return job is not None and job["status"] == "cancelled"


def _check_cancelled(pipeline: Pipeline, job_id: str) -> None:
    if _is_cancelled(pipeline, job_id):
        raise JobCancelled()


async def _set_stage(pipeline: Pipeline, job_id: str, stage: str) -> None:
    db.update_job_status(pipeline.conn, job_id, stage)
    pipeline.events.publish(job_id, {"type": "stage", "stage": stage})


async def _fetch_and_summarise(
    pipeline: Pipeline, job_id: str, subtopic_title: str, results: list[SearchResult],
) -> list[dict]:
    """Add each search result as a source row, fetch it, summarise it, and
    verify its excerpts. Returns the summaries that made it all the way
    through; a failure at any step for a given source is recorded on that
    source's row and the loop continues -- one dead source never fails the
    job (docs/spec/research.md).
    """
    summaries: list[dict] = []
    for r in results:
        _check_cancelled(pipeline, job_id)
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
    pipeline: Pipeline, job_id: str, depth_cfg: DepthConfig,
    seed_related_topics: list[str], citation_index: int,
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
            _check_cancelled(pipeline, job_id)
            results = await search.gather_sources(
                pipeline.http_client, pipeline.settings, [query],
                limit=depth_cfg.sources_per_subtopic,
                prioritise_papers=depth_cfg.prioritise_papers,
            )
            summaries = await _fetch_and_summarise(pipeline, job_id, query, results)
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


async def _resolve_topic(pipeline: Pipeline, job: dict) -> str:
    """'url -> fetch + extract -> derive topic; plain topic -> use as-is'
    (docs/spec/research.md, pipeline stage 1). If a topic was already
    given, it is used verbatim -- fetching the seed URL for topic
    derivation only happens when no topic was supplied.
    """
    if job["topic"] and job["topic"] != job["source_url"]:
        return job["topic"]

    url = job["source_url"]
    if not url:
        return job["topic"]

    doc = await fetch.fetch_document(pipeline.http_client, pipeline.settings, url)
    if doc is None or not doc.title:
        raise PipelineError(f"could not resolve a topic from seed URL: {url}")
    return doc.title


async def run_job(pipeline: Pipeline, job_id: str) -> None:
    conn = pipeline.conn
    job = db.get_job(conn, job_id)
    if job is None:
        return
    if job["status"] == "cancelled":
        return

    depth_cfg = get_depth_config(job["depth"])

    try:
        # ---- resolve + plan --------------------------------------------
        await _set_stage(pipeline, job_id, "planning")
        stage_id = db.start_stage(conn, job_id, "planning")

        topic = await _resolve_topic(pipeline, job)
        if topic != job["topic"]:
            slug = write.slugify(topic)
            db.update_job_topic(conn, job_id, topic, slug)

        outline = await llm.plan(
            pipeline.llm_client, settings=pipeline.settings,
            topic=topic, n_subtopics=depth_cfg.subtopics,
        )
        if outline is None:
            raise PipelineError("planning failed: the model did not return a usable outline")
        db.finish_stage(conn, stage_id, "done", detail=f"{len(outline.subtopics)} subtopics")

        # The slug is a directory name, bounded at SLUG_MAX_LEN. Deriving it
        # from the planner's short title keeps it readable instead of a
        # truncated sentence; the raw topic is the fallback when the model
        # gave no usable title.
        slug = write.slugify((outline.title or "").strip() or topic)
        db.update_job_slug(conn, job_id, slug)
        _check_cancelled(pipeline, job_id)

        # ---- searching ---------------------------------------------------
        await _set_stage(pipeline, job_id, "searching")
        stage_id = db.start_stage(conn, job_id, "searching")
        subtopic_results: dict[str, list[SearchResult]] = {}
        for st in outline.subtopics:
            _check_cancelled(pipeline, job_id)
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
        db.finish_stage(conn, stage_id, "done", detail=f"{total_sources} sources")
        _check_cancelled(pipeline, job_id)

        # ---- fetching + summarising, per subtopic ------------------------
        # Fetching and summarising are two distinct job states, but they
        # share one per-source loop (via _fetch_and_summarise) so a source
        # never gets fetched in one pass and summarised in a second pass
        # against a possibly-stale copy of its text.
        await _set_stage(pipeline, job_id, "fetching")
        fetch_stage_id = db.start_stage(conn, job_id, "fetching")
        await _set_stage(pipeline, job_id, "summarising")
        summarise_stage_id = db.start_stage(conn, job_id, "summarising")

        summaries_by_subtopic: dict[str, list[dict]] = {}
        n_summarised = 0
        for st in outline.subtopics:
            _check_cancelled(pipeline, job_id)
            summaries = await _fetch_and_summarise(
                pipeline, job_id, st.title, subtopic_results.get(st.title, []),
            )
            summaries_by_subtopic[st.title] = summaries
            n_summarised += len(summaries)
            pipeline.events.publish(
                job_id, {"type": "progress", "stage": "summarising",
                         "done": n_summarised, "total": total_sources},
            )

        db.finish_stage(conn, fetch_stage_id, "done", detail=f"{total_sources} sources")
        db.finish_stage(conn, summarise_stage_id, "done", detail=f"{n_summarised} summarised")
        _check_cancelled(pipeline, job_id)

        # ---- synthesising --------------------------------------------
        await _set_stage(pipeline, job_id, "synthesising")
        stage_id = db.start_stage(conn, job_id, "synthesising")

        note_specs: list[NoteSpec] = []
        all_citations: list[SourceCitation] = []
        all_related: list[str] = []
        all_open_questions: list[str] = []
        citation_index = 1

        for st in outline.subtopics:
            _check_cancelled(pipeline, job_id)
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
                pipeline, job_id, depth_cfg, all_related, citation_index,
            )
            note_specs.extend(recursed_notes)
            all_citations.extend(recursed_citations)

        db.finish_stage(conn, stage_id, "done", detail=f"{len(note_specs)} notes")
        _check_cancelled(pipeline, job_id)

        # ---- writing -----------------------------------------------------
        await _set_stage(pipeline, job_id, "writing")
        stage_id = db.start_stage(conn, job_id, "writing")
        dir_path = write.write_tree(
            Path(pipeline.settings.tree_dir), topic=topic, slug=slug, depth=job["depth"],
            model_id=pipeline.settings.llm_model, notes=note_specs,
            all_sources=all_citations,
            related_topics=all_related if depth_cfg.related_topics_note else None,
            open_questions=all_open_questions if depth_cfg.open_questions_note else None,
        )
        db.finish_stage(conn, stage_id, "done", detail=str(dir_path))

        db.update_job_status(conn, job_id, "ready")
        pipeline.events.publish(job_id, {"type": "stage", "stage": "ready"})

    except JobCancelled:
        logger.info("job %s cancelled", job_id)
        # status is already "cancelled" -- set by Pipeline.cancel()

    except PipelineError as exc:
        logger.warning("job %s failed: %s", job_id, exc)
        db.update_job_status(conn, job_id, "failed", error=str(exc))
        pipeline.events.publish(job_id, {"type": "failed", "error": str(exc)})

    except Exception as exc:  # noqa: BLE001 -- last resort: a job must never crash the worker
        logger.exception("job %s crashed", job_id)
        db.update_job_status(conn, job_id, "failed", error=f"internal error: {exc}")
        pipeline.events.publish(job_id, {"type": "failed", "error": str(exc)})
