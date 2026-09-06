"""Turn a gpt-researcher report body into the shape researchd requires:
Markdown citing its own sources with NOTE-LOCAL footnote markers `[^1]`,
`[^2]`, ... numbered from 1 in order of first appearance in that one body
(models.RemoteNote/RemoteSourceCitation, see engines/remote.py on the
researchd side). gpt-researcher itself never writes footnotes -- its own
prompts (prompts.py) instruct the model to cite in-text as a markdown
hyperlink placed right after the claim, e.g.:

    Spark runs jobs as a DAG of stages ([Spark Docs](https://spark.apache.org/docs)).

and to append its own "## References" list at the end. Neither shape is
useful downstream: researchd builds its own bibliography (Sources.md) from
`all_sources` and renders footnote *definitions* itself (write.py) from a
note's own `sources` list -- so every inline citation here is rewritten to a
bare `[^n]` marker, and gpt-researcher's own reference section is dropped
entirely (a second copy of it, unlinked to our numbering, would just be
dead weight in every note).
"""

from __future__ import annotations

import re

# Matches a markdown hyperlink citation, optionally wrapped in the single
# pair of parens gpt-researcher's own prompts ask for ("... ([text](url))."):
# the outer `\(...\)` alternative is tried first so it consumes the
# surrounding parens too, leaving no stray "()" behind; the bare form is the
# fallback for citations gpt-researcher did not parenthesize (its own
# "## References" list items are exactly this bare form) or a model that
# didn't follow the parenthesized convention.
_CITATION_RE = re.compile(
    r"""\((?:\[(?P<wrapped_text>[^\]]*)\]\((?P<wrapped_url>https?://[^\s()]+)\))\)
      | \[(?P<bare_text>[^\]]*)\]\((?P<bare_url>https?://[^\s()]+)\)""",
    re.VERBOSE,
)

# gpt-researcher's own reference section header (any level, "reference" or
# "references", case-insensitive) through the end of the body -- cut before
# footnoting so its bare-link citations don't get numbered as if they were
# body citations too.
_REFERENCES_SECTION_RE = re.compile(
    r"\n#{1,6}\s*references?\s*\n.*\Z", re.IGNORECASE | re.DOTALL,
)


def _strip_references_section(body: str) -> str:
    return _REFERENCES_SECTION_RE.sub("", body).rstrip()


def footnote_sources(body: str, source_titles: dict[str, str]) -> tuple[str, list[dict]]:
    """Rewrite `body`'s inline citations to `[^n]` markers and return the
    matching note-local `sources` list (RemoteSourceCitation shape, as
    plain dicts -- this module has no pydantic dependency of its own).

    `source_titles` maps a scraped URL to its real title (from gpt-
    researcher's `get_research_sources()`); a URL gpt-researcher cited but
    did not itself scrape (rare, but the model is free to quote a URL from
    a snippet it never fetched) falls back to the citation's own link text,
    then to the bare URL -- never dropped, since a citation with no source
    entry at all would leave a `[^n]` marker with no definition to render.

    A URL cited more than once in the same body reuses its first-assigned
    index -- multiple `[^n]` markers pointing at one footnote definition is
    normal Markdown, and gives write.py exactly one definition per source
    rather than duplicates.
    """
    body = _strip_references_section(body)

    order: dict[str, int] = {}  # url -> 1-based index, in first-appearance order
    titles: dict[str, str] = {}  # url -> the title we'll actually use

    def _replace(m: re.Match) -> str:
        url = m.group("wrapped_url") or m.group("bare_url")
        text = (m.group("wrapped_text") or m.group("bare_text") or "").strip()
        if url not in order:
            order[url] = len(order) + 1
            titles[url] = source_titles.get(url) or text or url
        return f"[^{order[url]}]"

    new_body = _CITATION_RE.sub(_replace, body)

    sources = [
        {"index": idx, "title": titles[url], "url": url, "doi": None, "excerpts": []}
        for url, idx in sorted(order.items(), key=lambda kv: kv[1])
    ]
    return new_body, sources
