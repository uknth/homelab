"""The engine registry: every `ResearchEngine` a running process knows
about, keyed by the same id stored on `jobs.engine` and accepted in
`POST /api/research`. Built once, from `Pipeline`, so every engine shares
the process's one db connection and one pair of HTTP clients rather than
each opening its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import EngineResult, ResearchEngine
from .native import NativeEngine
from .remote import RemoteEngine

if TYPE_CHECKING:
    from ..pipeline import Pipeline

__all__ = ["EngineResult", "ResearchEngine", "NativeEngine", "RemoteEngine", "build_registry"]


def build_registry(pipeline: "Pipeline") -> dict[str, ResearchEngine]:
    registry: dict[str, ResearchEngine] = {"native": NativeEngine(pipeline)}

    if pipeline.settings.gptr_url:
        registry["gptr"] = RemoteEngine(
            "gptr", "GPT Researcher", pipeline.settings.gptr_url, pipeline.http_client,
        )
    return registry
