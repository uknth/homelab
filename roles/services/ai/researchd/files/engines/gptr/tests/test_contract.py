"""The sidecar contract itself (main.py's docstring):

    POST /research                  -> {engine_job_id}
    GET  /research/{eid}            -> {status, stage, detail, error, result}
    POST /research/{eid}/cancel     -> {"ok": true}

`JobStore._run`'s state-machine transitions (running -> done, running ->
failed with an error, running -> failed via cancellation) are exercised
directly with asyncio, for deterministic control over exactly when the fake
research coroutine finishes -- driving the same transitions through a live
TestClient would mean racing a real background asyncio.Task against the
test's own assertions with no way to pause it mid-flight. The wire contract
(request/response shapes, 404s) is exercised separately, through TestClient,
below.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from gptr.engine import EngineError
from gptr.jobs import Job, JobStore


def _immediate_task(coro):
    return asyncio.create_task(coro)


# --------------------------------------------------------------------------
# JobStore._run -- state machine
# --------------------------------------------------------------------------


def test_job_transitions_running_to_done_with_result(monkeypatch):
    import gptr.jobs as jobs_mod

    async def fake_run_research(topic, depth, on_stage):
        on_stage("planning", "")
        on_stage("synthesising", "1 notes")
        return {"title": "X", "notes": [{"subtopic_title": "X", "body": "y", "sources": []}]}

    monkeypatch.setattr(jobs_mod, "run_research", fake_run_research)

    async def scenario():
        job = Job(id="j1", topic="X", depth=1)
        task = _immediate_task(JobStore()._run(job))
        # Not yet awaited: still mid-flight, must read as "running".
        assert job.status == "running"
        await task
        return job

    job = asyncio.run(scenario())
    assert job.status == "done"
    assert job.error is None
    assert job.result["title"] == "X"
    assert job.stage == "synthesising"


def test_job_transitions_running_to_failed_with_error_surfaced(monkeypatch):
    import gptr.jobs as jobs_mod

    async def fake_run_research(topic, depth, on_stage):
        on_stage("planning", "")
        raise EngineError("search backend unreachable")

    monkeypatch.setattr(jobs_mod, "run_research", fake_run_research)

    async def scenario():
        job = Job(id="j2", topic="X", depth=1)
        await JobStore()._run(job)
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed"
    assert job.error == "search backend unreachable"
    assert job.result is None


def test_job_transitions_running_to_failed_on_unexpected_exception(monkeypatch):
    import gptr.jobs as jobs_mod

    async def fake_run_research(topic, depth, on_stage):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_mod, "run_research", fake_run_research)

    async def scenario():
        job = Job(id="j3", topic="X", depth=1)
        await JobStore()._run(job)
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed"
    assert "boom" in job.error


def test_cancel_marks_job_failed_with_cancelled_error(monkeypatch):
    import gptr.jobs as jobs_mod

    started = asyncio.Event()

    async def fake_run_research(topic, depth, on_stage):
        on_stage("searching", "")
        started.set()
        await asyncio.sleep(100)  # never reached: the task gets cancelled first
        return {"title": "X", "notes": []}

    monkeypatch.setattr(jobs_mod, "run_research", fake_run_research)

    async def scenario():
        store = JobStore()
        job = Job(id="j4", topic="X", depth=1)
        job.task = _immediate_task(store._run(job))
        store._jobs[job.id] = job  # normally done by JobStore.start(); wired by hand here for a controlled task
        await started.wait()
        assert store.cancel("j4") is True
        await job.task
        assert store.cancel("j4") is False  # already finished -- nothing left to cancel
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed"
    assert job.error == "cancelled"


def test_job_store_cancel_returns_false_for_unknown_or_finished_job():
    store = JobStore()
    assert store.cancel("no-such-id") is False


# --------------------------------------------------------------------------
# The wire contract itself, through the real FastAPI routes
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    import gptr.jobs as jobs_mod
    import gptr.main as main_mod

    async def fake_run_research(topic, depth, on_stage):
        on_stage("planning", "")
        on_stage("synthesising", "1 notes")
        return {"title": "X", "notes": [{"subtopic_title": "X", "body": "y [^1]", "sources": [
            {"index": 1, "title": "t", "url": "https://x.example", "doi": None, "excerpts": []},
        ]}]}

    monkeypatch.setattr(jobs_mod, "run_research", fake_run_research)
    with TestClient(main_mod.app) as c:
        yield c


def test_healthz():
    import gptr.main as main_mod

    with TestClient(main_mod.app) as client:
        resp = client.get("/healthz")
    assert resp.json() == {"ok": True, "engine": "gptr"}


def test_full_round_trip_through_the_http_contract(client):
    resp = client.post("/research", json={"topic": "Spark", "depth": 2, "job_id": "researchd-job-1"})
    assert resp.status_code == 200
    engine_job_id = resp.json()["engine_job_id"]
    assert isinstance(engine_job_id, str) and engine_job_id

    for _ in range(200):
        poll = client.get(f"/research/{engine_job_id}").json()
        if poll["status"] != "running":
            break
    else:
        pytest.fail("job never left the running state")

    assert poll["status"] == "done"
    assert poll["error"] is None
    assert poll["result"]["title"] == "X"


def test_get_unknown_job_is_404(client):
    resp = client.get("/research/no-such-id")
    assert resp.status_code == 404


def test_cancel_unknown_job_is_404(client):
    resp = client.post("/research/no-such-id/cancel")
    assert resp.status_code == 404


def test_rejects_out_of_range_depth(client):
    resp = client.post("/research", json={"topic": "Spark", "depth": 9, "job_id": "j"})
    assert resp.status_code == 422


def test_rejects_empty_topic(client):
    resp = client.post("/research", json={"topic": "   ", "depth": 2, "job_id": "j"})
    assert resp.status_code == 422
