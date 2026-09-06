"""What run_research() hands back must validate as-is against researchd's
own RemoteEngineResult (imported directly, not approximated -- see
conftest.py) -- that schema is the actual contract engines/remote.py enforces
on the researchd side, so it is the only thing worth asserting against.

gpt_researcher.GPTResearcher is replaced with a small fake that never
touches a network, an LLM, or an embedding endpoint: conduct_research()
records one made-up scraped source per instance, write_report() cites it
with gpt-researcher's own parenthesized-hyperlink convention (prompts.py),
and get_subtopics() returns the `Subtopics`-shaped object construct_subtopics
produces on success (see engine.py's _subtopic_titles docstring).
"""

from __future__ import annotations

import asyncio

import gpt_researcher
import pytest
from researchd.models import RemoteEngineResult

from gptr.engine import run_research


class _FakeSubtopic:
    def __init__(self, task: str) -> None:
        self.task = task


class _FakeSubtopics:
    def __init__(self, tasks: list[str]) -> None:
        self.subtopics = [_FakeSubtopic(t) for t in tasks]


def _make_fake_researcher(subtopic_tasks: list[str]):
    class FakeGPTResearcher:
        def __init__(self, *, query, report_type, report_source=None, parent_query=None,
                     visited_urls=None, **kwargs):
            self.query = query
            self.report_type = report_type
            self.parent_query = parent_query
            self.visited_urls = set(visited_urls or set())
            self._sources: list[dict] = []

        async def conduct_research(self) -> None:
            url = f"https://source.example/{self.query.lower().replace(' ', '-')}"
            self.visited_urls.add(url)
            self._sources = [{"url": url, "title": f"Source for {self.query}", "raw_content": "x"}]

        async def get_subtopics(self):
            return _FakeSubtopics(subtopic_tasks)

        def get_research_sources(self) -> list[dict]:
            return self._sources

        async def write_report(self, existing_headers=None, relevant_written_contents=None) -> str:
            src = self._sources[0]
            return f"{self.query} matters ([{src['title']}]({src['url']})).\n\n## References\n\n[{src['url']}]({src['url']})\n"

    return FakeGPTResearcher


def test_multi_note_result_validates_against_remote_engine_result(monkeypatch):
    monkeypatch.setattr(gpt_researcher, "GPTResearcher", _make_fake_researcher(["Origins", "Applications"]))

    stages: list[tuple[str, str]] = []
    result = asyncio.run(run_research("Quantum Computing", depth=1, on_stage=lambda s, d: stages.append((s, d))))

    parsed = RemoteEngineResult.model_validate(result)  # raises on any schema violation
    assert len(parsed.notes) == 2
    assert {n.subtopic_title for n in parsed.notes} == {"Origins", "Applications"}
    for note in parsed.notes:
        assert "[^1]" in note.body
        assert "References" not in note.body  # gpt-researcher's own list was stripped
        assert len(note.sources) == 1
        assert note.sources[0].index == 1
    assert len(parsed.all_sources) == 2  # one distinct source per subtopic, job-wide
    assert parsed.title == "Quantum Computing"

    # Only the five documented stage names ever reach on_stage.
    allowed = {"planning", "searching", "fetching", "summarising", "synthesising"}
    assert {s for s, _ in stages} <= allowed


def test_single_note_fallback_when_no_subtopics_still_validates(monkeypatch, caplog):
    monkeypatch.setattr(gpt_researcher, "GPTResearcher", _make_fake_researcher([]))

    result = asyncio.run(run_research("Obscure Topic", depth=1, on_stage=lambda s, d: None))

    parsed = RemoteEngineResult.model_validate(result)
    assert len(parsed.notes) == 1  # the hard requirement: notes must be non-empty
    assert "[^1]" in parsed.notes[0].body


def test_result_title_is_a_short_noun_phrase_with_no_punctuation(monkeypatch):
    monkeypatch.setattr(gpt_researcher, "GPTResearcher", _make_fake_researcher([]))

    result = asyncio.run(run_research(
        "What is, exactly, the deal with Spark's shuffle behavior?", depth=1, on_stage=lambda s, d: None,
    ))

    assert len(result["title"].split()) <= 5
    assert not any(c in result["title"] for c in ",.?'\"")
