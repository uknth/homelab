"""A generic client for a sidecar research engine (GPT Researcher today,
or anything else that speaks this same three-endpoint contract). One class
serves both, because the contract is deliberately engine-agnostic: start a
job, poll it, translate its result -- nothing here knows anything GPT
Researcher-specific.

    POST {base_url}/research               {topic, depth, job_id} -> {engine_job_id}
    GET  {base_url}/research/{engine_job_id} -> {status, stage, detail, error, result}
    GET  {base_url}/healthz                  -> {ok, engine}

`result`, once `status == "done"`, is JSON from another container and
therefore exactly as untrusted as raw LLM output: it is schema-validated
against models.RemoteEngineResult (the same discipline models.py already
applies to the LLM's own JSON) before a single field of it reaches
write.py. A payload that fails that validation raises PipelineError --
never a partially-built EngineResult.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx
from pydantic import ValidationError

from ..models import ExcerptRecord, RemoteEngineResult, RemoteSourceCitation
from ..pipeline import JobCancelled, PipelineError
from ..write import NoteSpec, SourceCitation
from .base import EngineResult

logger = logging.getLogger("researchd.engines.remote")

# Per-request budget only. A sidecar job can legitimately run 40+ minutes --
# there is deliberately no overall deadline on the poll loop below, only on
# each individual HTTP call, so a slow-but-healthy engine is never killed
# out from under a job that is still making progress.
_REQUEST_TIMEOUT = httpx.Timeout(30.0)
_POLL_INTERVAL = 2.0


def _to_citation(s: RemoteSourceCitation) -> SourceCitation:
    # start/end are meaningless for a remote engine's excerpts -- they were
    # never sliced from character offsets the way excerpts.py does for the
    # native engine -- so they are zeroed rather than invented. Nothing
    # downstream (write.py's footnote rendering) reads them.
    return SourceCitation(
        index=s.index, title=s.title, url=s.url, doi=s.doi,
        excerpts=[ExcerptRecord(start=0, end=0, text=e.text, note=e.note or "") for e in s.excerpts],
    )


class RemoteEngine:
    def __init__(self, engine_id: str, label: str, base_url: str, http_client: httpx.AsyncClient) -> None:
        self.id = engine_id
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client

    async def healthy(self) -> bool:
        # Broad except is deliberate: a healthcheck's entire job is to turn
        # "sidecar unreachable / slow / returned garbage" into `False`, for
        # every reason that could happen, not just the ones enumerated here.
        try:
            resp = await self.http_client.get(f"{self.base_url}/healthz", timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return bool(isinstance(data, dict) and data.get("ok"))
        except Exception:  # noqa: BLE001 -- see comment above
            return False

    async def run(
        self, *, job_id: str, topic: str, depth: int,
        on_stage: Callable[[str, str], Awaitable[None]],
        check_cancelled: Callable[[], None],
    ) -> EngineResult:
        engine_job_id = await self._start(job_id=job_id, topic=topic, depth=depth)
        return await self._poll(engine_job_id, on_stage=on_stage, check_cancelled=check_cancelled)

    async def _start(self, *, job_id: str, topic: str, depth: int) -> str:
        try:
            resp = await self.http_client.post(
                f"{self.base_url}/research",
                json={"topic": topic, "depth": depth, "job_id": job_id},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data["engine_job_id"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise PipelineError(f"{self.label}: could not start the job: {exc}") from exc

    async def _poll(
        self, engine_job_id: str, *,
        on_stage: Callable[[str, str], Awaitable[None]],
        check_cancelled: Callable[[], None],
    ) -> EngineResult:
        last_stage: str | None = None
        last_detail: str | None = None
        try:
            while True:
                check_cancelled()

                try:
                    resp = await self.http_client.get(
                        f"{self.base_url}/research/{engine_job_id}", timeout=_REQUEST_TIMEOUT,
                    )
                    resp.raise_for_status()
                    poll = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise PipelineError(f"{self.label}: polling failed: {exc}") from exc

                stage = poll.get("stage") or self.id
                detail = poll.get("detail") or ""
                if stage != last_stage or detail != last_detail:
                    await on_stage(stage, detail)
                    last_stage, last_detail = stage, detail

                status = poll.get("status")
                if status == "failed":
                    raise PipelineError(poll.get("error") or f"{self.label} reported failure with no detail")
                if status == "done":
                    return self._parse_result(poll.get("result"))

                await asyncio.sleep(_POLL_INTERVAL)
        except JobCancelled:
            await self._cancel_best_effort(engine_job_id)
            raise

    async def _cancel_best_effort(self, engine_job_id: str) -> None:
        try:
            await self.http_client.post(
                f"{self.base_url}/research/{engine_job_id}/cancel", timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.info("%s: best-effort cancel of %s failed: %s", self.label, engine_job_id, exc)

    def _parse_result(self, raw: object) -> EngineResult:
        if raw is None:
            raise PipelineError(f"{self.label}: job reported done with no result")
        try:
            parsed = RemoteEngineResult.model_validate(raw)
        except ValidationError as exc:
            raise PipelineError(f"{self.label}: malformed result payload: {exc}") from exc

        notes = [
            NoteSpec(
                subtopic_title=n.subtopic_title, body=n.body,
                sources=[_to_citation(s) for s in n.sources],
            )
            for n in parsed.notes
        ]
        return EngineResult(
            title=parsed.title,
            notes=notes,
            all_sources=[_to_citation(s) for s in parsed.all_sources],
            related_topics=parsed.related_topics,
            open_questions=parsed.open_questions,
        )
