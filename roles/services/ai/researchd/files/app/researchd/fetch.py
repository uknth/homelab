"""Document fetching: httpx for transport, trafilatura for HTML text
extraction, pypdf for PDFs. This is the only module that talks to
arbitrary URLs the search stage returned -- never a URL supplied by the
model (see search.py's module docstring and docs/spec/research.md).

Fetch discipline: an honest User-Agent, robots.txt is checked before every
fetch, a hard per-request timeout, and a per-document byte cap enforced
while streaming (not after download) so a large file cannot exhaust
memory. Any failure at any step returns ``None`` -- the caller records a
per-source failure and moves on. One dead source must never fail a job.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

import httpx
import trafilatura
from pypdf import PdfReader

from .config import Settings
from .models import FetchedDoc

logger = logging.getLogger("researchd.fetch")

_ROBOTS_CACHE_TTL = 3600.0
_robots_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}


def _robots_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


async def _get_robots(client: httpx.AsyncClient, settings: Settings, url: str) -> urllib.robotparser.RobotFileParser | None:
    parts = urlsplit(url)
    cache_key = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()
    cached = _robots_cache.get(cache_key)
    if cached and now - cached[0] < _ROBOTS_CACHE_TTL:
        return cached[1]

    parser = urllib.robotparser.RobotFileParser()
    try:
        resp = await client.get(
            _robots_url(url), timeout=settings.fetch_timeout,
            headers={"User-Agent": settings.user_agent},
        )
        if resp.status_code >= 400:
            parser = None  # no robots.txt (or unreachable) -> nothing disallowed
        else:
            parser.parse(resp.text.splitlines())
    except httpx.HTTPError:
        parser = None

    _robots_cache[cache_key] = (now, parser)
    return parser


async def _allowed_by_robots(client: httpx.AsyncClient, settings: Settings, url: str) -> bool:
    parser = await _get_robots(client, settings, url)
    if parser is None:
        return True
    try:
        return parser.can_fetch(settings.user_agent, url)
    except Exception:  # malformed robots.txt should never block a fetch outright
        return True


async def _download(client: httpx.AsyncClient, settings: Settings, url: str) -> tuple[bytes, str] | None:
    headers = {"User-Agent": settings.user_agent, "Accept": "text/html,application/pdf,*/*"}
    try:
        async with client.stream("GET", url, headers=headers, timeout=settings.fetch_timeout,
                                  follow_redirects=True) as resp:
            if resp.status_code >= 400:
                return None
            content_type = resp.headers.get("content-type", "text/html").split(";")[0].strip()
            buf = BytesIO()
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > settings.fetch_max_bytes:
                    logger.info("fetch aborted, exceeded size cap: %s", url)
                    return None
                buf.write(chunk)
            return buf.getvalue(), content_type
    except httpx.HTTPError as exc:
        logger.info("fetch failed for %s: %s", url, exc)
        return None


def _extract_pdf_text(raw: bytes, *, max_pages: int = 60) -> str | None:
    try:
        reader = PdfReader(BytesIO(raw))
    except Exception as exc:  # pypdf raises a range of errors on bad PDFs
        logger.info("pdf parse failed: %s", exc)
        return None
    parts = []
    for page in reader.pages[:max_pages]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n".join(parts).strip()
    return text or None


def _extract_html_text(raw: bytes, url: str) -> tuple[str, str] | None:
    html = raw.decode("utf-8", errors="replace")
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
    if not text or not text.strip():
        return None
    title = None
    try:
        meta = trafilatura.extract_metadata(html)
        title = meta.title if meta else None
    except Exception:
        title = None
    return text.strip(), (title or url)


async def fetch_document(client: httpx.AsyncClient, settings: Settings, url: str) -> FetchedDoc | None:
    """Fetch and extract clean text from `url`, or return None on any
    failure -- robots disallowed, transport error, oversized body,
    unparseable content. Never raises.
    """
    try:
        if not await _allowed_by_robots(client, settings, url):
            logger.info("robots.txt disallows fetch: %s", url)
            return None
    except Exception as exc:
        logger.info("robots check failed for %s, skipping: %s", url, exc)
        return None

    downloaded = await _download(client, settings, url)
    if downloaded is None:
        return None
    raw, content_type = downloaded

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        text = _extract_pdf_text(raw)
        if not text:
            return None
        title = url.rsplit("/", 1)[-1] or url
        return FetchedDoc(url=url, title=title, text=text, content_type="application/pdf")

    extracted = _extract_html_text(raw, url)
    if extracted is None:
        return None
    text, title = extracted
    return FetchedDoc(url=url, title=title, text=text, content_type="text/html")
