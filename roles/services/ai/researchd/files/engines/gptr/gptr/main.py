"""FastAPI app: the fixed sidecar contract researchd's engines/remote.py
speaks (see that module for the client side, and ../../../app/researchd/
models.py's `Remote*` classes for the exact Result schema `/research/{eid}`
must eventually return).

    GET  /healthz                  -> {"ok": true, "engine": "gptr"}
    POST /research                 {topic, depth, job_id} -> {engine_job_id}
    GET  /research/{eid}           -> {status, stage, detail, error, result}
    POST /research/{eid}/cancel    -> {"ok": true}

No UI, no database, no published port -- this container is reached only by
researchd, over the compose project's internal network.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from .config import apply_env_defaults, settings
from .depth import MAX_DEPTH, MIN_DEPTH
from .jobs import jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    apply_env_defaults()
    yield


app = FastAPI(title="gptr", version="1.0.0", lifespan=lifespan)


class ResearchRequest(BaseModel):
    topic: str
    depth: int = 2
    job_id: str

    @field_validator("depth")
    @classmethod
    def depth_in_range(cls, v: int) -> int:
        if v < MIN_DEPTH or v > MAX_DEPTH:
            raise ValueError(f"depth must be between {MIN_DEPTH} and {MAX_DEPTH}")
        return v

    @field_validator("topic")
    @classmethod
    def topic_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("topic must not be empty")
        return v


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "engine": "gptr"}


@app.post("/research")
async def create_research(req: ResearchRequest) -> dict:
    # req.job_id (researchd's own job id) is accepted per the contract but
    # not otherwise used here -- this sidecar's own engine_job_id is what
    # every later call addresses, so the two ids are allowed to be, and
    # stay, unrelated.
    job = jobs.start(topic=req.topic, depth=req.depth)
    return {"engine_job_id": job.id}


@app.get("/research/{engine_job_id}")
async def get_research(engine_job_id: str) -> dict:
    job = jobs.get(engine_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@app.post("/research/{engine_job_id}/cancel")
async def cancel_research(engine_job_id: str) -> dict:
    if jobs.get(engine_job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    jobs.cancel(engine_job_id)
    # {"ok": true} regardless of whether there was still anything to cancel
    # (see JobStore.cancel's docstring) -- remote.py's best-effort cancel
    # POST does not inspect this body either way.
    return {"ok": True}


if __name__ == "__main__":
    # `python -m gptr.main` -- the Dockerfile's entrypoint. Port comes from
    # settings (GPTR_PORT) rather than a shell-expanded CMD, so the
    # container needs no shell to honour it.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
