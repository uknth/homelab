"""In-memory job store and the background-task plumbing around engine.py.
No persistence, by design (see the contract in main.py's docstring): a
restart loses in-flight jobs, and that is fine -- researchd's poll loop
(engines/remote.py) just sees the sidecar go unreachable/come back empty and
surfaces that as a failed job the same way any other sidecar outage would.

Kept deliberately dumb: a dict keyed by our own uuid4 engine_job_id, one
asyncio.Task per job, one lock-free status snapshot per job. Nothing here
needs a database for a process whose whole state fits in memory and is
allowed to vanish on restart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .engine import EngineError, run_research

logger = logging.getLogger("gptr.jobs")

Status = Literal["running", "done", "failed"]

# The stage names engine.py's on_stage callback uses, spelled out here only
# so main.py/tests have one place to check a reported stage is one of the
# set researchd's UI expects (main.py's docstring / the task spec).
STAGES = ("planning", "searching", "fetching", "summarising", "synthesising")


@dataclass
class Job:
    id: str
    topic: str
    depth: int
    status: Status = "running"
    stage: str = "planning"
    detail: str = ""
    error: str | None = None
    result: dict | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict:
        """The exact `GET /research/{eid}` response body."""
        return {
            "status": self.status, "stage": self.stage, "detail": self.detail,
            "error": self.error, "result": self.result,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def get(self, engine_job_id: str) -> Job | None:
        return self._jobs.get(engine_job_id)

    def start(self, *, topic: str, depth: int) -> Job:
        """Create a job and hand its background task to the running event
        loop immediately -- POST /research must return before the research
        itself finishes, per the contract.
        """
        job = Job(id=str(uuid.uuid4()), topic=topic, depth=depth)
        job.task = asyncio.create_task(self._run(job))
        self._jobs[job.id] = job
        return job

    def cancel(self, engine_job_id: str) -> bool:
        """Best-effort: cancel the underlying asyncio.Task, which raises
        CancelledError at whatever `await` engine.run_research is currently
        sitting on. Returns False for an unknown id or one already finished
        (nothing to cancel) -- both are the caller's cue to still treat the
        cancel as accepted (POST .../cancel returns {"ok": true} either way;
        see main.py) since the end state -- "this job is not going to
        produce more progress" -- is the same.
        """
        job = self._jobs.get(engine_job_id)
        if job is None or job.task is None or job.task.done():
            return False
        job.task.cancel()
        return True

    async def _run(self, job: Job) -> None:
        def on_stage(stage: str, detail: str) -> None:
            job.stage = stage
            job.detail = detail

        try:
            job.result = await run_research(job.topic, job.depth, on_stage)
            job.status = "done"
        except asyncio.CancelledError:
            # Contract has no "cancelled" status (main.py's docstring) --
            # researchd's own remote.py stops polling as soon as its local
            # cancellation fires regardless of what we report, but a
            # well-formed terminal status still has to be one of
            # running/done/failed, so this is reported as failed.
            job.status = "failed"
            job.error = "cancelled"
            logger.info("job %s (%r) cancelled", job.id, job.topic)
            # Deliberately not re-raised: this coroutine is the entire body
            # of its own asyncio.Task, nothing awaits it to propagate
            # cancellation further, so swallowing it here just ends the
            # task cleanly instead of leaving an "exception never retrieved"
            # warning on a Task nobody will ever await.
        except EngineError as exc:
            job.status = "failed"
            job.error = str(exc)
            logger.warning("job %s (%r) failed: %s", job.id, job.topic, exc)
        except Exception as exc:  # noqa: BLE001 -- any other bug must still surface as a failed job, never an unhandled task exception
            job.status = "failed"
            job.error = f"unexpected error: {exc}"
            logger.exception("job %s (%r) hit an unexpected error", job.id, job.topic)


jobs = JobStore()
