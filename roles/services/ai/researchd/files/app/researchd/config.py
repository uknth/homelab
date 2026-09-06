"""Environment-driven settings. Every tunable the Ansible role wires in
lands here, each with a default sane enough to run the app standalone.

The one secret this service holds -- the omlx API key -- is read here and
nowhere else; it is never logged and never reaches the model or a fetched
page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


def _float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val else default


@dataclass(frozen=True)
class Settings:
    # --- LLM (omlx, OpenAI-compatible) ---
    llm_base_url: str = os.environ.get("RESEARCHD_LLM_BASE_URL", "http://10.0.2.9:8000/v1")
    llm_api_key: str = os.environ.get("RESEARCHD_LLM_API_KEY", "")
    llm_model: str = os.environ.get(
        "RESEARCHD_LLM_MODEL", "Qwen3-30B-A3B-Instruct-2507-4bit"
    )
    llm_timeout: float = _float("RESEARCHD_LLM_TIMEOUT", 120.0)

    # --- search ---
    searxng_url: str = os.environ.get("RESEARCHD_SEARXNG_URL", "http://searxng:8080")
    search_timeout: float = _float("RESEARCHD_SEARCH_TIMEOUT", 15.0)

    # --- research engines ---
    # gptr is a sidecar container on the compose project network, not
    # yet built. Both are still registered by default (engines/__init__.py)
    # so the UI dropdown and /api/engines always list them; until a sidecar
    # actually exists at these urls, `healthy()` just reports them
    # unavailable rather than the registry omitting them outright.
    gptr_url: str = os.environ.get("RESEARCHD_GPTR_URL", "http://researchd-gptr:8111")
    default_engine: str = os.environ.get("RESEARCHD_DEFAULT_ENGINE", "gptr")

    # --- storage ---
    data_dir: str = os.environ.get("RESEARCHD_DATA_DIR", "/data")

    # --- server ---
    port: int = _int("RESEARCHD_PORT", 8110)

    # --- fetch discipline ---
    fetch_timeout: float = _float("RESEARCHD_FETCH_TIMEOUT", 20.0)
    fetch_max_bytes: int = _int("RESEARCHD_FETCH_MAX_BYTES", 3_000_000)
    user_agent: str = os.environ.get(
        "RESEARCHD_USER_AGENT",
        "researchd/1.0 (+https://research.puhome.net; homelab research agent; "
        "contact: homelab admin)",
    )

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "researchd.db")

    @property
    def tree_dir(self) -> str:
        return os.path.join(self.data_dir, "tree")

    @property
    def static_dir(self) -> str:
        # Shipped alongside the app in the image, not under /data.
        return os.environ.get("RESEARCHD_STATIC_DIR", "/app/static")


settings = Settings()
