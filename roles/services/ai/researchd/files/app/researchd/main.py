"""FastAPI app: routes, SSE progress stream, and the static UI mount.

Route surface matches docs/spec/research.md exactly:

    POST /api/research          {topic?, url?, depth}  -> {job_id}
    GET  /api/jobs                                      -> list with status
    GET  /api/jobs/{id}                                  -> detail + per-stage state
    GET  /api/jobs/{id}/events                           -> SSE progress stream
    GET  /api/jobs/{id}/bundle.tar.gz                    -> the markdown output
    POST /api/jobs/{id}/published   {wiki_url}           -> marks published
    POST /api/jobs/{id}/cancel
    GET  /healthz

`published` is set only by this module's `/published` handler, called by
n8n after the Quartz rebuild -- never by the pipeline itself (pipeline.py
only ever reaches `ready`).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tarfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import db
from .config import settings
from .models import JobCreated, PublishedRequest, ResearchRequest
from .pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("researchd.main")

pipeline = Pipeline(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await pipeline.start()
    logger.info("researchd started: model=%s data_dir=%s", settings.llm_model, settings.data_dir)
    try:
        yield
    finally:
        await pipeline.stop()


app = FastAPI(title="researchd", version="1.0.0", lifespan=lifespan)


def _job_summary(job: dict) -> dict:
    return {
        "id": job["id"],
        "topic": job["topic"],
        "source_url": job["source_url"],
        "depth": job["depth"],
        "slug": job["slug"],
        "status": job["status"],
        "error": job["error"],
        "wiki_url": job["wiki_url"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def _job_detail(job_id: str) -> dict:
    job = db.get_job(pipeline.conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    stages = db.list_stages(pipeline.conn, job_id)
    sources = db.list_sources(pipeline.conn, job_id)
    summarised = sum(1 for s in sources if s["status"] == "summarised")
    detail = _job_summary(job)
    detail["stages"] = stages
    detail["sources"] = sources
    detail["source_counts"] = {
        "total": len(sources), "summarised": summarised,
        "failed": sum(1 for s in sources if s["status"] == "failed"),
    }
    return detail


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@app.post("/api/research")
async def create_research(request: Request) -> JobCreated:
    try:
        payload = await request.json()
        req = ResearchRequest.model_validate(payload)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job_id = pipeline.submit(topic=req.topic, url=req.url, depth=req.depth)
    return JobCreated(job_id=job_id)


@app.get("/api/jobs")
async def list_jobs() -> list[dict]:
    return db.list_jobs(pipeline.conn)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    return _job_detail(job_id)


def _sse(data: dict) -> str:
    # Every event is sent as a plain, unnamed "message" -- the client
    # dispatches on the `type` field inside the JSON payload instead of on
    # separate SSE event names. One `onmessage` handler, one place that
    # decides what an event means.
    return f"data: {json.dumps(data)}\n\n"


_TERMINAL_STAGES = {"ready", "failed", "cancelled", "published"}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    job = db.get_job(pipeline.conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_stream():
        queue = pipeline.events.subscribe(job_id)
        try:
            yield _sse({"type": "snapshot", "job": _job_detail(job_id)})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event.get("type") == "failed" or event.get("stage") in _TERMINAL_STAGES:
                    break
        finally:
            pipeline.events.unsubscribe(job_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/bundle.tar.gz")
async def get_bundle(job_id: str) -> StreamingResponse:
    job = db.get_job(pipeline.conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in ("ready", "published"):
        raise HTTPException(status_code=409, detail=f"job is {job['status']}, not ready")

    tree_path = Path(settings.tree_dir) / job["slug"]
    if not tree_path.is_dir():
        raise HTTPException(status_code=404, detail="output tree missing on disk")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tree_path, arcname=job["slug"])
    buf.seek(0)

    headers = {"Content-Disposition": f'attachment; filename="{job["slug"]}.tar.gz"'}
    return StreamingResponse(buf, media_type="application/gzip", headers=headers)


@app.post("/api/jobs/{job_id}/published")
async def mark_published(job_id: str, body: PublishedRequest) -> dict:
    job = db.get_job(pipeline.conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in ("ready", "published"):
        raise HTTPException(status_code=409, detail=f"job is {job['status']}, not ready to publish")

    db.update_job_status(pipeline.conn, job_id, "published", wiki_url=body.wiki_url)
    pipeline.events.publish(job_id, {"type": "stage", "stage": "published", "wiki_url": body.wiki_url})
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    if not pipeline.cancel(job_id):
        raise HTTPException(status_code=409, detail="job cannot be cancelled (not found or already finished)")
    return {"ok": True}


@app.get("/healthz")
async def healthz() -> dict:
    try:
        pipeline.conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "model": settings.llm_model}


# --------------------------------------------------------------------------
# static UI -- mounted last so it never shadows an /api/* or /healthz route
# --------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")


if __name__ == "__main__":
    # `python -m researchd.main` -- the Dockerfile's entrypoint. Reading
    # the port from settings here (instead of a shell-expanded CMD) means
    # the container needs no shell to honour RESEARCHD_PORT.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
