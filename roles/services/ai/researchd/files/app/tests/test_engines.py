"""The engine seam (engines/) has two things worth pinning down with
tests that don't need a real sidecar or the resident model:

1. engines/remote.py crosses a trust boundary -- a sidecar's JSON result is
   schema-validated (models.RemoteEngineResult) exactly like raw LLM
   output, never partially trusted. httpx.MockTransport stands in for the
   sidecar so these run with no real network call.
2. A job whose `engine` column names an id no longer in the registry (a
   sidecar removed from the compose project, a stale value from before a
   rename) must fall back to `native`, not fail the job or crash the
   worker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from researchd.engines import remote as remote_mod
from researchd.engines.base import EngineResult
from researchd.engines.remote import RemoteEngine
from researchd.models import RemoteEngineResult
from researchd.pipeline import PipelineError, _lookup_engine

VALID_RESULT = {
    "title": "Spark Internals",
    "notes": [
        {
            "subtopic_title": "Execution Model",
            "body": "Spark runs jobs as a DAG of stages. [^1]",
            "sources": [
                {
                    "index": 1, "title": "Spark Docs", "url": "https://spark.apache.org/docs",
                    "excerpts": [{"text": "a stage is a set of tasks", "note": "definition"}],
                },
            ],
        },
    ],
    "all_sources": [
        {"index": 1, "title": "Spark Docs", "url": "https://spark.apache.org/docs", "excerpts": []},
    ],
    "related_topics": ["Spark shuffle"],
    "open_questions": ["How does adaptive query execution decide?"],
}


def _sequenced_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    """One canned response per call, in order, then repeats the last --
    good enough for a start-then-poll conversation against a single url.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    return httpx.MockTransport(handler)


async def _noop_on_stage(stage: str, detail: str) -> None:
    pass


def _never_cancelled() -> None:
    pass


# --------------------------------------------------------------------------
# RemoteEngine.run() -- the full start-then-poll-then-parse round trip
# --------------------------------------------------------------------------


def test_remote_engine_parses_a_valid_result_payload(monkeypatch):
    monkeypatch.setattr(remote_mod, "_POLL_INTERVAL", 0)
    client = httpx.AsyncClient(transport=_sequenced_transport([
        httpx.Response(200, json={"engine_job_id": "eid1"}),
        httpx.Response(200, json={
            "status": "running", "stage": "searching", "detail": "", "error": None, "result": None,
        }),
        httpx.Response(200, json={
            "status": "done", "stage": "synthesising", "detail": "1 notes",
            "error": None, "result": VALID_RESULT,
        }),
    ]))
    engine = RemoteEngine("gptr", "GPT Researcher", "http://sidecar:8111", client)

    stages: list[tuple[str, str]] = []

    async def on_stage(stage: str, detail: str) -> None:
        stages.append((stage, detail))

    result = asyncio.run(engine.run(
        job_id="job1", topic="Spark", depth=2,
        on_stage=on_stage, check_cancelled=_never_cancelled,
    ))

    assert isinstance(result, EngineResult)
    assert result.title == "Spark Internals"
    assert [n.subtopic_title for n in result.notes] == ["Execution Model"]
    note = result.notes[0]
    assert note.body.startswith("Spark runs jobs")
    assert len(note.sources) == 1
    assert note.sources[0].title == "Spark Docs"
    assert note.sources[0].excerpts[0].text == "a stage is a set of tasks"
    assert note.sources[0].excerpts[0].note == "definition"
    assert len(result.all_sources) == 1
    assert result.related_topics == ["Spark shuffle"]
    assert result.open_questions == ["How does adaptive query execution decide?"]
    assert ("searching", "") in stages
    assert ("synthesising", "1 notes") in stages


def test_remote_engine_missing_notes_raises_pipeline_error_not_partial_data(monkeypatch):
    monkeypatch.setattr(remote_mod, "_POLL_INTERVAL", 0)
    client = httpx.AsyncClient(transport=_sequenced_transport([
        httpx.Response(200, json={"engine_job_id": "eid2"}),
        httpx.Response(200, json={
            "status": "done", "stage": "writing", "detail": "done",
            "error": None, "result": {"title": "X"},  # no "notes" key at all
        }),
    ]))
    engine = RemoteEngine("gptr", "GPT Researcher", "http://sidecar:8111", client)

    with pytest.raises(PipelineError):
        asyncio.run(engine.run(
            job_id="job2", topic="X", depth=1,
            on_stage=_noop_on_stage, check_cancelled=_never_cancelled,
        ))


def test_remote_engine_reported_failure_raises_pipeline_error(monkeypatch):
    monkeypatch.setattr(remote_mod, "_POLL_INTERVAL", 0)
    client = httpx.AsyncClient(transport=_sequenced_transport([
        httpx.Response(200, json={"engine_job_id": "eid3"}),
        httpx.Response(200, json={
            "status": "failed", "stage": "searching", "detail": "",
            "error": "search backend unreachable", "result": None,
        }),
    ]))
    engine = RemoteEngine("gptr", "GPT Researcher", "http://sidecar:8111", client)

    with pytest.raises(PipelineError, match="search backend unreachable"):
        asyncio.run(engine.run(
            job_id="job3", topic="X", depth=1,
            on_stage=_noop_on_stage, check_cancelled=_never_cancelled,
        ))


def test_remote_engine_healthy_reports_false_without_raising_when_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = RemoteEngine("gptr", "GPT Researcher", "http://sidecar:8111", client)

    assert asyncio.run(engine.healthy()) is False


def test_remote_engine_healthy_reports_true_when_sidecar_says_ok():
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True, "engine": "gptr"}),
    ))
    engine = RemoteEngine("gptr", "GPT Researcher", "http://sidecar:8111", client)

    assert asyncio.run(engine.healthy()) is True


# --------------------------------------------------------------------------
# RemoteEngineResult -- the schema boundary directly (wrong types / oversized)
# --------------------------------------------------------------------------


def test_remote_engine_result_rejects_wrong_field_types():
    with pytest.raises(ValidationError):
        RemoteEngineResult.model_validate({"notes": "not-a-list"})


def test_remote_engine_result_rejects_oversized_notes():
    with pytest.raises(ValidationError):
        RemoteEngineResult.model_validate({
            "notes": [{"subtopic_title": "x", "body": "y", "sources": []} for _ in range(2001)],
        })


def test_remote_engine_result_rejects_oversized_all_sources():
    with pytest.raises(ValidationError):
        RemoteEngineResult.model_validate({
            "notes": [],
            "all_sources": [
                {"index": i, "title": "t", "url": "https://x.example"} for i in range(20_001)
            ],
        })


# --------------------------------------------------------------------------
# registry fallback (pipeline.py)
# --------------------------------------------------------------------------


class _FakeEngine:
    def __init__(self, engine_id: str, label: str) -> None:
        self.id = engine_id
        self.label = label

    async def healthy(self) -> bool:
        return True

    async def run(self, **kwargs):  # pragma: no cover -- never expected to be called here
        raise AssertionError("engine.run() should not be invoked by _lookup_engine")


def test_lookup_engine_falls_back_to_native_for_an_unknown_id(caplog):
    native = _FakeEngine("native", "Built-in")
    engines = {"native": native, "gptr": _FakeEngine("gptr", "GPT Researcher")}

    resolved = _lookup_engine(engines, "removed-sidecar", job_id="job-x")

    assert resolved is native


def test_lookup_engine_returns_the_requested_engine_when_known():
    native = _FakeEngine("native", "Built-in")
    gptr = _FakeEngine("gptr", "GPT Researcher")
    engines = {"native": native, "gptr": gptr}

    assert _lookup_engine(engines, "gptr", job_id="job-y") is gptr


# --------------------------------------------------------------------------
# GET /api/engines -- shape, and a down sidecar must not raise
# --------------------------------------------------------------------------


def test_list_engines_endpoint_reports_unavailable_without_raising(monkeypatch, tmp_path):
    # config.settings is a module-level singleton, and every *field*'s
    # default is a `os.environ.get(...)` expression evaluated once, when
    # the Settings class body first executes -- not per instantiation, so
    # setting the env vars now and merely re-calling Settings() would not
    # pick them up (static_dir is the one exception: a @property that reads
    # its env var live). Passing values as constructor kwargs instead is
    # what actually overrides them, then rebinding config.settings is what
    # main.py's own `from .config import settings` picks up, since main.py
    # has not been imported anywhere yet in this test session.
    monkeypatch.setenv(
        "RESEARCHD_STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static"),
    )

    import researchd.config as config_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config_mod, "settings", config_mod.Settings(
        data_dir=str(tmp_path),
        gptr_url="http://127.0.0.1:1",  # nothing listens here
        default_engine="native",
    ))

    import researchd.main as main_mod

    with TestClient(main_mod.app) as client:
        resp = client.get("/api/engines")

    assert resp.status_code == 200
    body = resp.json()
    by_id = {e["id"]: e for e in body}
    assert set(by_id) == {"native", "gptr"}
    assert by_id["native"]["available"] is True
    assert by_id["native"]["is_default"] is True
    assert by_id["gptr"]["available"] is False
    assert by_id["gptr"]["is_default"] is False


def test_stage_sweep_closes_stages_the_engine_never_finished(tmp_path):
    """A remote engine that starts stages and never reports them finishing
    must not leave `active` rows behind once the job is done.

    This is not hypothetical: gptr reports planning/searching/summarising
    starting and then moves on without a finishing detail, which left a
    completed job showing three stages still running in the UI. The sweep in
    _make_on_stage/run_job is what makes stage rows the pipeline's
    responsibility rather than each engine author's.
    """
    import asyncio

    from researchd import db
    from researchd.pipeline import _make_on_stage

    conn = db.connect(str(tmp_path / "t.db"))
    db.create_job(
        conn, job_id="j1", topic="t", source_url=None, depth=1,
        slug="T", status="running", engine="gptr",
    )

    class _FakePipeline:
        def __init__(self, conn):
            self.conn = conn
            self.events = type("E", (), {"publish": staticmethod(lambda *a, **k: None)})()

    on_stage, close_pending = _make_on_stage(_FakePipeline(conn), "j1")

    asyncio.run(on_stage("planning", ""))     # started, never finished
    asyncio.run(on_stage("searching", ""))    # started, never finished
    asyncio.run(on_stage("writing", ""))
    asyncio.run(on_stage("writing", "/data/tree/T"))   # properly finished

    assert {s["stage"] for s in db.list_stages(conn, "j1") if s["status"] == "active"} == {
        "planning", "searching",
    }

    close_pending("done", None)

    stages = db.list_stages(conn, "j1")
    assert [s for s in stages if s["status"] == "active"] == []
    assert {s["stage"]: s["status"] for s in stages} == {
        "planning": "done", "searching": "done", "writing": "done",
    }
