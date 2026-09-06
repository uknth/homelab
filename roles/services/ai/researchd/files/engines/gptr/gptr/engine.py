"""The gpt-researcher orchestration: one topic in, one Result dict out
(the exact shape models.RemoteEngineResult validates on the researchd side --
see ../../../app/researchd/models.py). Nothing in here talks HTTP; jobs.py
owns the job dict and the stage-reporting plumbing, engine.py owns gpt-
researcher itself.

Confirmed against gpt-researcher 0.16.0 (pip), by introspecting the
installed package directly rather than trusting memory -- there is no
`DetailedReport`/`SubtopicReport` orchestrator class in this pip package
(that class lives only in the full github.com/assafelovic/gpt-researcher
checkout's backend/, which is not installed by `pip install gpt-researcher`).
The subtopic flow below is hand-rolled from the primitives the installed
`GPTResearcher` class does expose (`get_subtopics`, `report_type=
"subtopic_report"`, shared `visited_urls`), which is the same pattern that
class's own docstrings and the report_type enum describe.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .citations import footnote_sources
from .depth import DepthConfig, get_depth_config

logger = logging.getLogger("gptr.engine")

# gpt-researcher configures itself from process-wide os.environ at every
# GPTResearcher() construction (config.py has no constructor kwarg for it).
# MAX_SEARCH_RESULTS_PER_QUERY/MAX_ITERATIONS/MAX_SUBTOPICS are the one part
# of that config that varies per job (by depth) rather than per process, so
# two jobs of different depth running concurrently would race on those env
# vars and corrupt each other's search budget. This lock serializes a whole
# job's gpt-researcher work (not just the env mutation) behind one job at a
# time in this sidecar -- correctness over throughput, which is the right
# trade for a single homelab sidecar where jobs already run for minutes.
JOB_LOCK = asyncio.Lock()

_TITLE_WORD_RE = re.compile(r"[A-Za-z0-9]+")


class EngineError(Exception):
    """Raised on any unrecoverable gpt-researcher failure; jobs.py catches
    this (and only this, plus asyncio.CancelledError) to distinguish "the
    research failed" from an actual bug in this sidecar's own code.
    """


OnStage = Callable[[str, str], None]


def short_title(topic: str, max_words: int = 5) -> str:
    """A short noun-phrase title, <= max_words, no punctuation -- it becomes
    a directory name on the researchd side (write.py's slugify()), so it is
    derived the same way write.py derives slugs: every alphanumeric run is a
    word, everything else is a separator and disappears. Deliberately not an
    LLM call: one more model round-trip that can fail is not worth it for
    five words that can be pulled out of the topic string directly.
    """
    words = _TITLE_WORD_RE.findall(topic)[:max_words]
    return " ".join(words) if words else "Research"


def _subtopic_titles(raw: object, limit: int) -> list[str]:
    """Normalise whatever `GPTResearcher.get_subtopics()` returned into a
    plain list of title strings, capped at `limit`.

    Confirmed shape (gpt_researcher/utils/llm.py:construct_subtopics): on
    success this is a `Subtopics` pydantic model (`.subtopics` -> list of
    `Subtopic(task=str)`); on any internal failure (a bad LLM response, a
    parse error) it instead returns whatever `subtopics` list was passed in
    -- `[]` for our main researcher, since we never seed one. Both shapes,
    and a bare list of dicts/strings for good measure, are handled here so a
    version skew in gpt-researcher's return shape degrades to the single-
    note fallback (empty titles) rather than raising.
    """
    items = getattr(raw, "subtopics", raw) or []
    titles: list[str] = []
    for item in items:
        if hasattr(item, "task"):
            t = item.task
        elif isinstance(item, dict):
            t = item.get("task") or item.get("title") or ""
        elif isinstance(item, str):
            t = item
        else:
            continue
        t = t.strip()
        if t:
            titles.append(t)
    return titles[:limit]


def _source_titles(sources: list[dict]) -> dict[str, str]:
    """`GPTResearcher.get_research_sources()` -> {url: title}, falling back
    to the url itself when gpt-researcher's own scrape produced no title
    (confirmed shape: gpt_researcher/scraper/scraper.py always emits a
    `title` key, but it is frequently an empty string for a page with no
    <title>).
    """
    out: dict[str, str] = {}
    for s in sources:
        url = s.get("url")
        if url:
            out[url] = (s.get("title") or "").strip() or url
    return out


def _apply_depth_env(cfg: DepthConfig) -> None:
    os.environ["MAX_SEARCH_RESULTS_PER_QUERY"] = str(cfg.max_search_results_per_query)
    os.environ["MAX_ITERATIONS"] = str(cfg.max_iterations)
    os.environ["MAX_SUBTOPICS"] = str(cfg.subtopics)


@dataclass
class _Note:
    subtopic_title: str
    body: str
    sources: list[dict] = field(default_factory=list)


def _merge_all_sources(notes: list[_Note]) -> list[dict]:
    """Job-wide numbering, deliberately independent of any note's own
    note-local numbering (models.RemoteEngineResult docstring, researchd
    side) -- the same URL cited by two different notes gets one entry here,
    numbered by first appearance across the whole job.
    """
    seen: dict[str, dict] = {}
    for note in notes:
        for src in note.sources:
            if src["url"] not in seen:
                seen[src["url"]] = {
                    "index": len(seen) + 1, "title": src["title"], "url": src["url"],
                    "doi": src["doi"], "excerpts": [],
                }
    return list(seen.values())


async def run_research(topic: str, depth: int, on_stage: OnStage) -> dict:
    """Run one job to completion and return a Result dict matching
    models.RemoteEngineResult exactly. Raises EngineError on failure;
    asyncio.CancelledError (from jobs.py cancelling our task) propagates
    through whichever `await` it lands on, uncaught here on purpose --
    jobs.py is what decides a cancelled job's reported status.
    """
    # Imported lazily, not at module load: importing gpt_researcher pulls in
    # its full dependency tree (langchain, retrievers, ...) at first use
    # rather than at process start, and tests for citations.py/depth.py never
    # need it at all.
    from gpt_researcher import GPTResearcher

    depth_cfg = get_depth_config(depth)

    async with JOB_LOCK:
        _apply_depth_env(depth_cfg)

        on_stage("planning", "")
        main = GPTResearcher(query=topic, report_type="research_report", report_source="web")
        try:
            await main.conduct_research()
        except Exception as exc:  # noqa: BLE001 -- any gpt-researcher internal failure is fatal to the job
            raise EngineError(f"initial research failed: {exc}") from exc

        raw_subtopics = await main.get_subtopics()
        titles = _subtopic_titles(raw_subtopics, depth_cfg.subtopics)
        on_stage("planning", f"{len(titles)} subtopics" if titles else "no subtopics")

        notes: list[_Note] = []
        if not titles:
            # Degrade gracefully: carry the whole report as one note rather
            # than fail the job because an optional multi-note path (which
            # depends on the model successfully proposing subtopics) came
            # back empty.
            logger.warning(
                "gpt-researcher returned no subtopics for %r; falling back to a single note", topic,
            )
            on_stage("summarising", "")
            try:
                body = await main.write_report()
            except Exception as exc:  # noqa: BLE001
                raise EngineError(f"report writing failed: {exc}") from exc
            new_body, sources = footnote_sources(body, _source_titles(main.get_research_sources()))
            notes.append(_Note(subtopic_title=short_title(topic, max_words=8) or topic, body=new_body, sources=sources))
            on_stage("summarising", "1 note (fallback)")
        else:
            visited: set[str] = set(main.visited_urls)
            for i, sub_title in enumerate(titles, start=1):
                on_stage("searching", "")
                sub = GPTResearcher(
                    query=sub_title, report_type="subtopic_report", parent_query=topic,
                    visited_urls=set(visited),
                )
                try:
                    await sub.conduct_research()
                except Exception as exc:  # noqa: BLE001
                    raise EngineError(f"research on subtopic {sub_title!r} failed: {exc}") from exc
                visited |= sub.visited_urls
                on_stage("searching", f"{i}/{len(titles)}: {sub_title}")

                on_stage("fetching", "")
                sources_raw = sub.get_research_sources()
                on_stage("fetching", f"{len(sources_raw)} sources")

                on_stage("summarising", "")
                try:
                    body = await sub.write_report(existing_headers=[], relevant_written_contents=[])
                except Exception as exc:  # noqa: BLE001
                    raise EngineError(f"writing subtopic {sub_title!r} failed: {exc}") from exc
                new_body, note_sources = footnote_sources(body, _source_titles(sources_raw))
                notes.append(_Note(subtopic_title=sub_title, body=new_body, sources=note_sources))
                on_stage("summarising", f"{i}/{len(titles)}: {sub_title}")

        on_stage("synthesising", "")
        result = {
            "title": short_title(topic),
            "notes": [
                {"subtopic_title": n.subtopic_title, "body": n.body, "sources": n.sources}
                for n in notes
            ],
            "all_sources": _merge_all_sources(notes),
            # gpt-researcher has no equivalent of researchd's own
            # related-topics/open-questions synthesise step (llm.py on the
            # researchd side) -- left empty rather than invented from
            # nothing; both fields are optional on RemoteEngineResult.
            "related_topics": [],
            "open_questions": [],
        }
        on_stage("synthesising", f"{len(notes)} notes")
        return result
