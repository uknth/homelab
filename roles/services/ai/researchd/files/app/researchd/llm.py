"""The omlx client -- the only place the LLM is ever called, and the only
three shapes it is ever called in: plan, summarise, synthesise.

Every response is parsed as JSON (plan, summarise) or treated as opaque
prose (synthesise) and, for the JSON calls, validated against a pydantic
model from models.py before a single field of it is used anywhere else.
Anything that fails to parse or validate is discarded -- logged and
returned as ``None``, never retried with the bad data, never partially
trusted. This module contains no `eval`, no `exec`, no subprocess, no
shell of any kind: it is an HTTP client and a JSON parser, nothing more.

omlx has a tight ~2.6 GB KV budget for a 30B MoE model, so a prompt that is
too large comes back as HTTP 400 with an error code containing
`prefill_memory_exceeded`. `summarise()` handles that by shrinking the
slice of the document it sends and retrying -- never by crashing the job.
"""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import ValidationError

from .config import Settings
from .models import PlanOutline, SummariseResult, SynthesiseOutcome

logger = logging.getLogger("researchd.llm")

# Always summarise from the start of the document; a chunk that starts at
# offset 0 means the offsets the model returns are already valid global
# offsets into the full document text, with no translation step needed.
INITIAL_CHUNK_CHARS = 12_000
MIN_CHUNK_CHARS = 1_500
CHUNK_SHRINK_FACTOR = 0.5
MAX_CHUNK_ATTEMPTS = 5


class LLMError(Exception):
    """The call itself failed: network error, bad status, malformed
    transport-level response. Distinct from "the model said something we
    don't trust", which is not an exception at all -- see _extract_json."""


class PrefillMemoryExceeded(Exception):
    """omlx rejected the prompt: it would not fit the KV cache budget."""


def make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.llm_base_url,
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        timeout=settings.llm_timeout,
    )


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"{err.get('code', '')} {err.get('message', '')}".strip()
    return str(err or body)[:300]


async def _chat(
    client: httpx.AsyncClient, *, model: str, messages: list[dict],
    max_tokens: int, temperature: float = 0.2, json_mode: bool = False,
) -> str:
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        resp = await client.post("/chat/completions", json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"transport error calling omlx: {exc}") from exc

    if resp.status_code == 400:
        detail = _error_detail(resp)
        if "prefill_memory_exceeded" in detail.lower():
            raise PrefillMemoryExceeded(detail)
        raise LLMError(f"HTTP 400 from omlx: {detail}")
    if resp.status_code >= 400:
        raise LLMError(f"HTTP {resp.status_code} from omlx: {resp.text[:300]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise LLMError(f"non-JSON completion response: {exc}") from exc

    # A 2xx does NOT mean success. omlx's "pre-chunk guard" rejection path
    # returns HTTP 200 with an error body instead of a 4xx — verified against
    # ai01 on 2026-09-04, status 200 carrying
    #   {"error": {"code": "prefill_memory_exceeded", ...}, "type": "error"}
    # Because the checks above only look at the status code, this fell through
    # to data["choices"] and surfaced as "malformed completion response:
    # 'choices'" — which reads like a parser bug and hides a memory-guard
    # rejection that the retry loop below already knows how to handle. Every
    # summarise call in the first real research job failed this way.
    if isinstance(data, dict) and data.get("error") is not None:
        detail = _error_detail(resp)
        if "prefill_memory_exceeded" in detail.lower():
            raise PrefillMemoryExceeded(detail)
        raise LLMError(f"omlx returned an error with HTTP {resp.status_code}: {detail}")

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"malformed completion response: {exc}") from exc


def _extract_json(raw: str) -> dict | list | None:
    """The model is asked for a bare JSON object; be forgiving of a stray
    code fence and nothing else. Anything that still fails to parse is
    discarded here -- this is half of the schema-validate-or-discard
    contract, the other half being the pydantic models it is checked
    against next.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

PLAN_SYSTEM = (
    "You are a research planning assistant. Given a topic, produce a JSON "
    "outline and nothing else: no prose, no markdown code fences, no "
    "commentary before or after the JSON."
)


def _plan_prompt(topic: str, n_subtopics: int) -> str:
    return (
        f'Topic: "{topic}"\n\n'
        f"Produce exactly {n_subtopics} subtopics that together give a "
        "technical reader good coverage of this topic. Respond with ONLY "
        "this JSON object, no other text:\n"
        '{"title": "...", "subtopics": [{"title": "...", "scope": "one '
        'sentence", "search_queries": ["query one", "query two"]}]}\n\n'
        'The top-level "title" is a short noun-phrase name for the topic '
        "as a whole -- at most 5 words, no punctuation, no question mark -- "
        "because it becomes a directory name.\n\n"
        f"Exactly {n_subtopics} entries in \"subtopics\". 2-4 "
        "search_queries each, phrased as search-engine queries (keywords), "
        "not questions."
    )


async def plan(
    client: httpx.AsyncClient, *, settings: Settings, topic: str, n_subtopics: int,
) -> PlanOutline | None:
    try:
        raw = await _chat(
            client, model=settings.llm_model,
            messages=[
                {"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": _plan_prompt(topic, n_subtopics)},
            ],
            max_tokens=1500, json_mode=True,
        )
    except (LLMError, PrefillMemoryExceeded) as exc:
        logger.warning("plan call failed for %r: %s", topic, exc)
        return None

    data = _extract_json(raw)
    if data is None:
        logger.warning("plan response was not valid JSON for %r", topic)
        return None
    # Shape coercion, NOT content trust. Asked for {"subtopics": [...]}, the
    # model frequently returns the bare array instead -- observed live on the
    # very first real job: PlanOutline got `[{'title': 'Setting up Spark'...}]`
    # and the job failed at planning with "did not return a usable outline".
    #
    # Wrapping a top-level list is safe because it changes only the container,
    # never a value: every element still has to satisfy PlanSubtopic below, and
    # anything that doesn't is still discarded. Being strict about JSON shape
    # buys no security here -- the security property is that model output can
    # only ever become schema-validated data, and that is unchanged.
    if isinstance(data, list):
        data = {"subtopics": data}

    try:
        outline = PlanOutline.model_validate(data)
    except ValidationError as exc:
        logger.warning("plan response failed schema validation for %r: %s", topic, exc)
        return None

    if len(outline.subtopics) > n_subtopics:
        outline.subtopics = outline.subtopics[:n_subtopics]
    return outline


# --------------------------------------------------------------------------
# summarise
# --------------------------------------------------------------------------

SUMMARISE_SYSTEM = (
    "You summarise one document for a research note. Respond with ONLY a "
    "JSON object: no prose, no markdown code fences, no commentary."
)


def _summarise_prompt(title: str, url: str, chunk: str) -> str:
    return (
        f"Source: {title or url}\nURL: {url}\n\n"
        f"--- document text (may be truncated) ---\n{chunk}\n--- end ---\n\n"
        "Write a 150-250 word summary of the document's content, relevant "
        "to a research reader. Then choose up to 3 short passages worth "
        "quoting verbatim (each under 400 characters) and report them as "
        "character offsets into the document text shown above -- NOT the "
        "quoted text itself. Offsets are 0-indexed and end-exclusive, "
        "counted over the exact text between the '--- document text ---' "
        "and '--- end ---' markers.\n\n"
        "Respond with ONLY this JSON object:\n"
        '{"summary": "...", "excerpts": [{"start": 0, "end": 0, "note": '
        '"why this passage matters, one sentence"}]}'
    )


async def summarise(
    client: httpx.AsyncClient, *, settings: Settings, title: str, url: str, doc_text: str,
) -> tuple[SummariseResult | None, str]:
    """Returns ``(result, chunk_used)``. ``chunk_used`` is the exact text
    the model was shown -- always ``doc_text[:n]`` for some ``n``, i.e. a
    prefix starting at offset 0 -- so the offsets in ``result.excerpts``
    are already valid global offsets into ``doc_text`` with no translation
    needed. The caller (pipeline.py) still runs every offset through
    excerpts.extract_and_verify before trusting it.
    """
    chunk_chars = INITIAL_CHUNK_CHARS
    last_error: Exception | None = None

    for _ in range(MAX_CHUNK_ATTEMPTS):
        chunk = doc_text[:chunk_chars]
        try:
            raw = await _chat(
                client, model=settings.llm_model,
                messages=[
                    {"role": "system", "content": SUMMARISE_SYSTEM},
                    {"role": "user", "content": _summarise_prompt(title, url, chunk)},
                ],
                max_tokens=800, json_mode=True,
            )
        except PrefillMemoryExceeded as exc:
            last_error = exc
            chunk_chars = int(chunk_chars * CHUNK_SHRINK_FACTOR)
            if chunk_chars < MIN_CHUNK_CHARS:
                break
            logger.info("prefill exceeded for %s; retrying with %d chars", url, chunk_chars)
            continue
        except LLMError as exc:
            logger.warning("summarise call failed for %s: %s", url, exc)
            return None, chunk

        data = _extract_json(raw)
        if data is None:
            logger.warning("summarise response was not valid JSON for %s", url)
            return None, chunk
        try:
            result = SummariseResult.model_validate(data)
        except ValidationError as exc:
            logger.warning("summarise response failed schema validation for %s: %s", url, exc)
            return None, chunk
        return result, chunk

    logger.warning("summarise gave up on %s after shrinking chunk: %s", url, last_error)
    return None, doc_text[:MIN_CHUNK_CHARS]


# --------------------------------------------------------------------------
# synthesise
# --------------------------------------------------------------------------

SYNTHESISE_SYSTEM = (
    "You write one research note in Markdown from a set of source "
    "summaries. Cite every claim with a footnote marker like [^1] matching "
    "the numbered source list you are given. Do not invent facts beyond "
    "what the summaries support. Write prose only -- no frontmatter, no "
    "top-level title heading."
)


def _synthesise_prompt(
    subtopic_title: str, scope: str, summaries: list[dict],
    allow_related: bool, related_budget: int, allow_open_questions: bool,
) -> str:
    numbered = "\n\n".join(
        f"[{i + 1}] {s['title']} ({s['url']})\n{s['summary']}"
        for i, s in enumerate(summaries)
    )
    tail = ""
    if allow_related:
        tail += (
            "\n\nAfter the note body, on its own line, write the marker "
            '"RELATED_TOPICS:" followed by a JSON array of up to '
            f"{related_budget} short search-query strings for related "
            "topics worth researching next. Use an empty array if none."
        )
    if allow_open_questions:
        tail += (
            "\n\nThen, on its own line, write the marker "
            '"OPEN_QUESTIONS:" followed by a JSON array of up to 5 short '
            "strings naming questions this material leaves unanswered. "
            "Use an empty array if none."
        )
    return (
        f"Subtopic: {subtopic_title}\nScope: {scope}\n\n"
        f"Numbered sources:\n{numbered}\n\n"
        "Write the note body now (Markdown). Reference sources as "
        f"[^1], [^2], etc. matching the numbers above.{tail}"
    )


def _parse_marker_list(raw: str, marker: str) -> tuple[str, list[str]]:
    """Pull a `MARKER: [...]` tail off the end of `raw`, if present, and
    return (remaining_text, parsed_list). The JSON array is schema-checked
    (must be a JSON list of strings); anything else yields an empty list
    and the marker line is still stripped from the body so it never leaks
    into the note as stray text.
    """
    idx = raw.rfind(marker)
    if idx == -1:
        return raw, []
    body = raw[:idx].rstrip()
    tail = raw[idx + len(marker):].strip()
    parsed = _extract_json(tail)
    if isinstance(parsed, list):
        items = [q.strip() for q in parsed if isinstance(q, str) and q.strip()]
    else:
        items = []
    return body, items


async def synthesise(
    client: httpx.AsyncClient, *, settings: Settings, subtopic_title: str, scope: str,
    summaries: list[dict], allow_related: bool, related_budget: int,
    allow_open_questions: bool,
) -> SynthesiseOutcome:
    try:
        raw = await _chat(
            client, model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYNTHESISE_SYSTEM},
                {"role": "user", "content": _synthesise_prompt(
                    subtopic_title, scope, summaries, allow_related,
                    related_budget, allow_open_questions,
                )},
            ],
            max_tokens=2200,
        )
    except (LLMError, PrefillMemoryExceeded) as exc:
        logger.warning("synthesise call failed for %r: %s", subtopic_title, exc)
        return SynthesiseOutcome(body="")

    body = raw
    related: list[str] = []
    open_questions: list[str] = []

    if allow_open_questions:
        body, open_questions = _parse_marker_list(body, "OPEN_QUESTIONS:")
    if allow_related:
        body, related = _parse_marker_list(body, "RELATED_TOPICS:")

    try:
        return SynthesiseOutcome.model_validate({
            "body": body.strip(),
            "related_topics": related[:related_budget],
            "open_questions": open_questions,
        })
    except ValidationError as exc:
        logger.warning("synthesise outcome failed schema validation for %r: %s", subtopic_title, exc)
        return SynthesiseOutcome(body=body.strip())
