"""Stage orchestration: the fixed pipeline that turns one job into a
markdown tree.

`queued -> planning -> searching -> fetching -> summarising ->
synthesising -> writing -> ready`, plus `failed` / `cancelled`.
`published` is set only by the external callback endpoint (main.py),
never by anything in this module. Same for the `publishing` status in
between `ready` and `published` -- entirely owned by main.py's
`/claim` and `/unclaim` endpoints, which this module never calls and
never checks for.

Concurrency is 1: a single asyncio worker task pulls job ids off an
in-process queue and runs them one at a time, because there is exactly
one resident model on ai01 and this pipeline is the only thing allowed to
call it. The queue is backed by SQLite (`db.list_active_jobs`), so a
container restart resumes cleanly -- see `Pipeline.start`.

What actually produces a job's notes -- plan/search/fetch/summarise/
synthesise, or a call out to a sidecar -- lives behind the `ResearchEngine`
seam (engines/), one per `jobs.engine` value. This module owns everything
that applies identically no matter which engine ran: resolving a seed URL
into a topic, deciding the job's slug, writing the resulting tree to disk,
and the terminal status transitions. It is also still the one place that
model-proposed related topics are ever treated as anything other than a
search-query string (see engines/native.py's `_run_recursion`, which this
module used to contain directly) -- see docs/spec/research.md, "The model
never chooses an action".
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

from . import db, fetch, llm, write
from .config import Settings

if TYPE_CHECKING:
    from .engines import ResearchEngine

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
    one for search + fetch, also reused by remote engines to reach their
    sidecar), the event bus, the engine registry, and the single worker
    task. One instance lives for the lifetime of the process (see main.py).
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

        # Built last, once conn/http_client/llm_client all exist: engines
        # (engines/native.py in particular) need live access to all three.
        # Imported here rather than at module top for the same circular-
        # import reason as llm.py above -- engines/native.py imports
        # PipelineError/JobCancelled from this module, so this module must
        # finish defining itself before engines/ is ever imported.
        from .engines import build_registry
        self.engines: dict[str, "ResearchEngine"] = build_registry(self)

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

    def submit(self, *, topic: str | None, url: str | None, depth: int, engine: str) -> str:
        job_id = db.new_job_id()
        label = topic or url or "untitled"
        slug = write.slugify(label)
        db.create_job(
            self.conn, job_id=job_id, topic=label, source_url=url,
            depth=depth, slug=slug, status="queued", engine=engine,
        )
        self.queue.put_nowait(job_id)
        return job_id

    def cancel(self, job_id: str) -> bool:
        job = db.get_job(self.conn, job_id)
        # `publishing` is blocked alongside `ready`/`published`: once n8n has
        # claimed the job the SSH ingest is running against whatever is
        # already on disk, entirely outside this process's control. Setting
        # status to `cancelled` here would not stop that ingest, and would
        # instead pull the rug out from under /unclaim and /published --
        # both of which are about to be called by a run that has no idea the
        # job was "cancelled" out from under it.
        if job is None or job["status"] in ("ready", "publishing", "published", "failed", "cancelled"):
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


def _make_on_stage(
    pipeline: Pipeline, job_id: str,
) -> tuple[Callable[[str, str], Awaitable[None]], Callable[[str, str | None], None]]:
    """Build the `on_stage` callback handed to `ResearchEngine.run`. This is
    where `_set_stage` + `db.start_stage` / `db.finish_stage` now live --
    they used to be called directly, inline, all over the old run_job; an
    engine calls this instead, at exactly the same points.

    Contract: an empty `detail` means `stage` has just started (mirrors
    `_set_stage` + `db.start_stage`); a non-empty `detail` means the most
    recently started occurrence of `stage` has finished, with `detail` as
    its summary (mirrors `db.finish_stage`). Tracked as a dict keyed by
    stage name, not a single "current stage" var, because the native engine
    keeps `fetching` and `summarising` both active in the db at once for a
    stretch of every job.

    Returns `(on_stage, close_pending)`. `close_pending` exists because that
    contract trusts the ENGINE to report a finishing detail for every stage
    it starts, and a third-party sidecar simply does not: gptr reports
    `planning`/`searching`/`summarising` starting and then moves on, so those
    rows sat `active` forever and the UI showed a finished job with three
    stages apparently still running. Rather than make every engine
    responsible for perfect bookkeeping, run_job sweeps whatever is still
    open once the engine returns -- so a stage row can never outlive the job
    that owns it, whoever wrote the engine.
    """
    active: dict[str, int] = {}

    async def on_stage(stage: str, detail: str) -> None:
        if stage not in active:
            await _set_stage(pipeline, job_id, stage)
            active[stage] = db.start_stage(pipeline.conn, job_id, stage)
        if detail:
            stage_id = active.pop(stage, None)
            if stage_id is not None:
                db.finish_stage(pipeline.conn, stage_id, "done", detail=detail)

    def close_pending(status: str, detail: str | None) -> None:
        for stage, stage_id in list(active.items()):
            db.finish_stage(pipeline.conn, stage_id, status, detail=detail)
            active.pop(stage, None)

    return on_stage, close_pending


def _lookup_engine(
    engines: dict[str, "ResearchEngine"], engine_id: str, *, job_id: str,
) -> "ResearchEngine":
    """A job row can outlive an engine being removed (a sidecar taken out
    of the compose project, a typo'd id from an old client) -- fall back to
    native rather than failing a job outright over a missing engine.
    """
    engine = engines.get(engine_id)
    if engine is None:
        logger.warning(
            "job %s requested unknown engine %r; falling back to native", job_id, engine_id,
        )
        return engines["native"]
    return engine


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

    on_stage, close_pending = _make_on_stage(pipeline, job_id)

    def check_cancelled() -> None:
        _check_cancelled(pipeline, job_id)

    engine = _lookup_engine(pipeline.engines, job["engine"], job_id=job_id)

    try:
        # ---- resolve + plan --------------------------------------------
        await on_stage("planning", "")

        topic = await _resolve_topic(pipeline, job)
        if topic != job["topic"]:
            slug = write.slugify(topic)
            db.update_job_topic(conn, job_id, topic, slug)

        result = await engine.run(
            job_id=job_id, topic=topic, depth=job["depth"],
            on_stage=on_stage, check_cancelled=check_cancelled,
        )

        # The slug is a directory name, bounded at SLUG_MAX_LEN. Deriving it
        # from the engine's short title keeps it readable instead of a
        # truncated sentence; the raw topic is the fallback when the engine
        # gave no usable title.
        slug = write.slugify((result.title or "").strip() or topic)
        db.update_job_slug(conn, job_id, slug)
        check_cancelled()

        # ---- writing -----------------------------------------------------
        await on_stage("writing", "")
        # `engine` is the object _lookup_engine actually resolved, not
        # job["engine"] as requested -- the unknown-id fallback above can
        # silently swap in native, and a page claiming an engine that did
        # not write it is worse than no label at all.
        dir_path = write.write_tree(
            Path(pipeline.settings.tree_dir), topic=topic, slug=slug, depth=job["depth"],
            model_id=pipeline.settings.llm_model, engine=engine.id, engine_label=engine.label,
            notes=result.notes, all_sources=result.all_sources,
            related_topics=result.related_topics or None,
            open_questions=result.open_questions or None,
        )
        await on_stage("writing", str(dir_path))

        # Anything the engine started but never reported finishing (see
        # _make_on_stage) is closed here, before the job is called ready.
        close_pending("done", None)
        db.update_job_status(conn, job_id, "ready")
        pipeline.events.publish(job_id, {"type": "stage", "stage": "ready"})

    except JobCancelled:
        close_pending("failed", "cancelled")
        logger.info("job %s cancelled", job_id)
        # status is already "cancelled" -- set by Pipeline.cancel()

    except PipelineError as exc:
        close_pending("failed", str(exc))
        logger.warning("job %s failed: %s", job_id, exc)
        db.update_job_status(conn, job_id, "failed", error=str(exc))
        pipeline.events.publish(job_id, {"type": "failed", "error": str(exc)})

    except Exception as exc:  # noqa: BLE001 -- last resort: a job must never crash the worker
        close_pending("failed", str(exc))
        logger.exception("job %s crashed", job_id)
        db.update_job_status(conn, job_id, "failed", error=f"internal error: {exc}")
        pipeline.events.publish(job_id, {"type": "failed", "error": str(exc)})
