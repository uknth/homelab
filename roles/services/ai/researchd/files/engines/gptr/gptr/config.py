"""Environment-driven settings, same spirit as researchd's own config.py:
every tunable the Ansible role wires in lands here, each with a default sane
enough to run the sidecar standalone.

Most of these names are NOT our invention -- they are the exact environment
variables gpt-researcher's own `Config` class (gpt_researcher/config/config.py)
reads directly from `os.environ` at every `GPTResearcher()` construction, with
no constructor kwarg to inject configuration any other way. That is why
`apply_env_defaults` below pushes each of these into `os.environ` (via
`setdefault`, so an operator-supplied value always wins) rather than this
module just holding them for our own reference -- if we didn't, an unset env
var would silently fall back to gpt-researcher's *own* defaults (tavily
retriever, gpt-5.4 models, OpenAI's real API) instead of ours.

`EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY` are the one pair here that gpt-
researcher does NOT read directly -- see the big comment on
`embedding_kwargs_json` for why they have to be threaded through
`EMBEDDING_KWARGS` instead of the more obvious `OPENAI_BASE_URL`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


@dataclass(frozen=True)
class Settings:
    # --- server ---
    port: int = _int("GPTR_PORT", 8111)

    # --- LLM (omlx, OpenAI-compatible) ---
    # Read by gpt_researcher.config.config.Config and by
    # gpt_researcher.llm_provider.generic.base directly -- see apply_env_defaults.
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "http://10.0.2.9:8000/v1")
    # "not-needed" rather than "": langchain_openai's client wants *a* string,
    # and omlx (a local llama.cpp/vLLM-style server) does not check it.
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "not-needed")
    fast_llm: str = os.environ.get("FAST_LLM", "openai:Qwen3-30B-A3B-Instruct-2507-4bit")
    smart_llm: str = os.environ.get("SMART_LLM", "openai:Qwen3-30B-A3B-Instruct-2507-4bit")
    strategic_llm: str = os.environ.get("STRATEGIC_LLM", "openai:Qwen3-30B-A3B-Instruct-2507-4bit")

    # --- embeddings ---
    # `EMBEDDING` is gpt-researcher's own var: "<provider>:<model>". The base
    # URL for that provider is NOT one of gpt-researcher's own env vars for
    # the "openai" provider -- it falls back to OPENAI_BASE_URL unless told
    # otherwise (see embedding_kwargs_json). embedding_base_url/api_key below
    # are this sidecar's own invention, purely to carry that override.
    embedding: str = os.environ.get("EMBEDDING", "openai:bge-small-en-v1.5")
    embedding_base_url: str = os.environ.get("EMBEDDING_BASE_URL", "http://researchd-embed:8082/v1")
    embedding_api_key: str = os.environ.get("EMBEDDING_API_KEY", "not-needed")

    # --- search ---
    retriever: str = os.environ.get("RETRIEVER", "searx,arxiv")
    searx_url: str = os.environ.get("SEARX_URL", "http://searxng:8080")

    # The SERVED embedding model's real context window, in tokens. See
    # embedding_kwargs_json() below for why this must not be left at
    # langchain's OpenAI-tuned default of 8191.
    embedding_ctx_length: int = _int("EMBEDDING_CTX_LENGTH", 512)


settings = Settings()


def embedding_kwargs_json() -> str:
    """The `EMBEDDING_KWARGS` env var gpt-researcher's `Config` reads (a JSON
    object merged as **kwargs into `OpenAIEmbeddings(...)`).

    Why this has to exist at all: `Memory.__init__`'s "openai" branch
    (gpt_researcher/memory/embeddings.py) does

        if "openai_api_base" not in embedding_kwargs and os.environ.get("OPENAI_BASE_URL"):
            embedding_kwargs["openai_api_base"] = os.environ["OPENAI_BASE_URL"]

    -- i.e. an embedding provider with no explicit base URL of its own
    silently inherits the LLM's OPENAI_BASE_URL. Here that would point the
    bge-small-en-v1.5 embedding calls at the Qwen chat endpoint (omlx),
    which does not serve embeddings at all. Setting `openai_api_base` (and
    `openai_api_key`, so it doesn't fall back to a real OPENAI_API_KEY either)
    inside EMBEDDING_KWARGS up front means that inheritance branch's `not in`
    check is already False by the time Memory runs, so it never fires.

    `check_embedding_ctx_length` is the setting that actually matters here, and
    it must be False against llama.cpp -- see the comment on it below.
    `embedding_ctx_length` is set alongside it for honesty about the served
    model's real window; with chunking already done upstream it is not load
    bearing, because gpt-researcher's compressor pipeline is
    [RecursiveCharacterTextSplitter(chunk_size=1000), EmbeddingsFilter] --
    every page is split into ~1000-character (~250-token) chunks BEFORE
    anything is embedded, so no input near any model's context ever reaches
    the endpoint.
    """
    return json.dumps({
        "openai_api_base": settings.embedding_base_url,
        "openai_api_key": settings.embedding_api_key,
        "embedding_ctx_length": settings.embedding_ctx_length,
        # MUST be False against llama.cpp, and this single flag is the whole
        # bug. Left at its default of True, langchain posts INTEGER TOKEN
        # ARRAYS rather than text -- legal against OpenAI, which shares that
        # vocabulary; llama.cpp reads the same integers as ids in the SERVED
        # model's vocabulary and rejects the request. It surfaces as two
        # different errors depending on the model, and the first one is
        # actively misleading:
        #     bge-small (512 ctx): "input (1202 tokens) is too large"
        #                          -- that is the ARRAY LENGTH, not real text,
        #                          so it reads like a context-size problem and
        #                          is not one. Do not go model-shopping for it;
        #                          a longer-context model was tried and changed
        #                          nothing.
        #     longer-ctx model:    "Prompt contains invalid tokens"
        #                          -- the same arrays, ids now out of range.
        # False makes langchain send plain strings and both disappear.
        #
        # Nothing is lost by disabling langchain's own chunking: the caller
        # already chunked. See the docstring above.
        "check_embedding_ctx_length": False,
    })


def apply_env_defaults() -> None:
    """Push this sidecar's own defaults into os.environ for every var
    gpt-researcher's Config reads directly, but only via `setdefault` --
    an operator (the Ansible role, docker-compose) setting the real env var
    always wins. Idempotent; safe to call more than once (tests do, via
    monkeypatch + reimport).
    """
    os.environ.setdefault("OPENAI_BASE_URL", settings.openai_base_url)
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    os.environ.setdefault("FAST_LLM", settings.fast_llm)
    os.environ.setdefault("SMART_LLM", settings.smart_llm)
    os.environ.setdefault("STRATEGIC_LLM", settings.strategic_llm)
    os.environ.setdefault("EMBEDDING", settings.embedding)
    os.environ.setdefault("RETRIEVER", settings.retriever)
    os.environ.setdefault("SEARX_URL", settings.searx_url)
    # Always ours, never an operator's to set directly -- it is derived from
    # embedding_base_url/embedding_api_key above, not an independent knob.
    os.environ["EMBEDDING_KWARGS"] = embedding_kwargs_json()
