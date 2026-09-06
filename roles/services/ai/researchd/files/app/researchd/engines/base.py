"""The seam between pipeline.py (orchestration: stages, db rows, the
writer) and however a job's research actually gets done. `ResearchEngine`
is the contract every engine -- native or a sidecar reached over HTTP --
must satisfy; `EngineResult` is the one shape every engine hands back,
regardless of what it did internally.

Nothing in this module talks to the model, the db, or the filesystem --
that is still pipeline.py's job (via `on_stage`/`check_cancelled`, and via
write.py once an EngineResult comes back). This module only defines the
shape of the seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from ..write import NoteSpec, SourceCitation


@dataclass
class EngineResult:
    """What one engine run produces. `title` is a short noun phrase (or
    None if the engine has no opinion) that pipeline.py slugifies into the
    job's directory name, exactly like `outline.title` did before this
    engine seam existed. Everything else feeds write.write_tree verbatim.
    """

    title: str | None
    notes: list[NoteSpec]
    all_sources: list[SourceCitation]
    related_topics: list[str]
    open_questions: list[str]


class ResearchEngine(Protocol):
    """`id` is the stable key stored on the job row (db.jobs.engine) and
    used in the `/api/research` request body; `label` is what the UI shows
    in the engine dropdown.
    """

    id: str
    label: str

    async def healthy(self) -> bool:
        """Never raises -- a down or unreachable engine must report False,
        not blow up `GET /api/engines` for every other engine in the
        registry."""
        ...

    async def run(
        self,
        *,
        job_id: str,
        topic: str,
        depth: int,
        on_stage: Callable[[str, str], Awaitable[None]],
        check_cancelled: Callable[[], None],
    ) -> EngineResult:
        """Do the research. `on_stage(stage, detail)` must be awaited at
        every point of progress the engine wants reflected in the job's
        stage rows and SSE stream; `check_cancelled()` must be called
        between phases so a cancelled job unwinds promptly instead of
        running to completion unattended. Raises PipelineError (pipeline.py)
        on unrecoverable failure, JobCancelled if check_cancelled() does.
        """
        ...
