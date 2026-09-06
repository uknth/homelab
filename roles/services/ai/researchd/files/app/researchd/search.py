"""Search fan-out: SearXNG (general web) plus four reference APIs -- arXiv,
OpenAlex, Crossref, Wikipedia -- and Open Library for books. Every query
string that reaches this module is either typed by a human (the initial
topic/subtopic search terms come from the plan LLM call, schema-validated
in models.py) or, at depth 4/5, a related-topic string proposed by the
synthesise call -- in both cases it is *only ever* a search query, never a
URL. This module never fetches anything but a search API's own result
list; document fetching happens in fetch.py, from URLs the search engines
themselves returned.

Every engine call is independently wrapped so one dead API cannot fail a
job -- see the "one dead source must never fail a job" rule in the spec.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import httpx

from .config import Settings
from .models import SearchResult

logger = logging.getLogger("researchd.search")

_TIMEOUT_MARGIN = httpx.Timeout(15.0)


async def _get(client: httpx.AsyncClient, url: str, *, params: dict | None = None,
                headers: dict | None = None) -> httpx.Response | None:
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=_TIMEOUT_MARGIN)
        resp.raise_for_status()
        return resp
    except httpx.HTTPError as exc:
        logger.info("search engine call failed (%s): %s", url, exc)
        return None


async def search_searxng(client: httpx.AsyncClient, settings: Settings, query: str, limit: int) -> list[SearchResult]:
    resp = await _get(
        client, f"{settings.searxng_url.rstrip('/')}/search",
        params={"q": query, "format": "json"},
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    out = []
    for item in (data.get("results") or [])[:limit]:
        url = item.get("url")
        if not url:
            continue
        out.append(SearchResult(
            title=item.get("title") or url, url=url, engine="searxng",
            snippet=(item.get("content") or "")[:300],
        ))
    return out


async def search_arxiv(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    resp = await _get(
        client, "http://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "max_results": limit},
    )
    if resp is None:
        return []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("a:entry", ns)[:limit]:
        title_el = entry.find("a:title", ns)
        id_el = entry.find("a:id", ns)
        summary_el = entry.find("a:summary", ns)
        if id_el is None or not (id_el.text or "").strip():
            continue
        out.append(SearchResult(
            title=(title_el.text or id_el.text or "").strip().replace("\n", " "),
            url=id_el.text.strip(), engine="arxiv",
            snippet=((summary_el.text or "").strip().replace("\n", " "))[:300],
        ))
    return out


async def search_openalex(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    resp = await _get(
        client, "https://api.openalex.org/works",
        params={"search": query, "per-page": limit},
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    out = []
    for item in (data.get("results") or [])[:limit]:
        url = (item.get("primary_location") or {}).get("landing_page_url") or item.get("id")
        if not url:
            continue
        doi = item.get("doi")
        if doi:
            doi = doi.replace("https://doi.org/", "")
        out.append(SearchResult(
            title=item.get("display_name") or url, url=url, engine="openalex", doi=doi,
        ))
    return out


async def search_crossref(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    resp = await _get(
        client, "https://api.crossref.org/works",
        params={"query": query, "rows": limit},
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    out = []
    for item in ((data.get("message") or {}).get("items") or [])[:limit]:
        url = item.get("URL")
        if not url:
            continue
        title = " ".join(item.get("title") or []) or url
        out.append(SearchResult(title=title, url=url, engine="crossref", doi=item.get("DOI")))
    return out


async def search_wikipedia(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    resp = await _get(
        client, "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": limit},
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    out = []
    for item in ((data.get("query") or {}).get("search") or [])[:limit]:
        title = item.get("title")
        if not title:
            continue
        url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        out.append(SearchResult(title=title, url=url, engine="wikipedia"))
    return out


async def search_open_library(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    resp = await _get(
        client, "https://openlibrary.org/search.json",
        params={"q": query, "limit": limit},
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    out = []
    for item in (data.get("docs") or [])[:limit]:
        key = item.get("key")
        if not key:
            continue
        out.append(SearchResult(
            title=item.get("title") or key, url=f"https://openlibrary.org{key}",
            engine="open_library",
        ))
    return out


_PAPER_ENGINES = {"arxiv", "openalex", "crossref"}


def _dedup(results: list[SearchResult]) -> list[SearchResult]:
    seen_urls: set[str] = set()
    seen_dois: set[str] = set()
    out = []
    for r in results:
        url_key = r.url.rstrip("/").lower()
        doi_key = (r.doi or "").strip().lower() or None
        if url_key in seen_urls:
            continue
        if doi_key and doi_key in seen_dois:
            continue
        seen_urls.add(url_key)
        if doi_key:
            seen_dois.add(doi_key)
        out.append(r)
    return out


async def gather_sources(
    client: httpx.AsyncClient, settings: Settings, queries: list[str], *,
    limit: int, prioritise_papers: bool = False,
) -> list[SearchResult]:
    """Run every query against every engine, dedup by URL and DOI, and
    return at most `limit` results. Each engine call is independently
    fault-tolerant -- a dead engine contributes an empty list, never an
    exception that would fail the search stage.
    """
    per_engine = max(3, limit)
    results: list[SearchResult] = []
    for query in queries:
        results.extend(await search_searxng(client, settings, query, per_engine))
        results.extend(await search_arxiv(client, query, per_engine))
        results.extend(await search_openalex(client, query, per_engine))
        results.extend(await search_crossref(client, query, per_engine))
        results.extend(await search_wikipedia(client, query, per_engine))
        results.extend(await search_open_library(client, query, per_engine))

    results = _dedup(results)
    if prioritise_papers:
        results.sort(key=lambda r: 0 if r.engine in _PAPER_ENGINES else 1)
    return results[:limit]
