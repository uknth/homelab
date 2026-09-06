"""Markdown tree assembly: slugs, frontmatter, the folder note (MOC),
the bibliography, and per-subtopic notes.

Nothing here calls the model. Every string written to disk either came
from our own code (headings, callouts, frontmatter) or was already
validated/extracted upstream: `body` text is LLM prose treated strictly as
opaque text (never parsed as markup with side effects), and excerpt text
was sliced and verified in excerpts.py before it ever reached this module.

Internal wikilinks are the one thing this module must get exactly right.
Every note file is named after a hyphenated slug (e.g.
`Spark-Execution-Model.md`), but the human-readable title is not that
slug (e.g. "Spark Execution Model"). A wikilink written as `[[Spark
Execution Model]]` will never resolve to that file -- vaultmerge resolves
links by filename stem, not by title text. So every internal link this
module emits uses the alias form `[[<file-slug>|<Title>]]`, and every
slug used in a link is the exact same slug used to name the file, computed
once and threaded through both call sites (see `_slug_map` below) so the
two can never drift apart.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .models import ExcerptRecord

# The job slug becomes a directory name that research-pull.sh (on cmp01)
# validates against ^[A-Za-z0-9_-]{1,64}$ before it will extract the bundle.
# A slug over that length deadlocks the job at `ready` forever: the puller
# rejects it, so n8n never calls /published. 60 rather than 64 leaves room for
# _dedupe_slug's -2..-99 suffix to stay inside the same bound.
SLUG_MAX_LEN = 60

# A wikilink this module writes always has this shape: [[slug|Title]].
# Exported so tests (and nothing else) can find every link a rendered
# document contains and check it against the files actually written.
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")

SOURCES_SLUG = "Sources"
RELATED_TOPICS_SLUG = "Related-Topics"
OPEN_QUESTIONS_SLUG = "Open-Questions"

_MAX_HISTORY = 20


@dataclass
class SourceCitation:
    """One numbered source as it appears in a note's footnotes, or as one
    line in Sources.md. `index` matches the [^n] marker the synthesise
    call was told to use for this source.
    """

    index: int
    title: str
    url: str
    doi: str | None = None
    excerpts: list[ExcerptRecord] = field(default_factory=list)


@dataclass
class NoteSpec:
    """One subtopic note: the synthesised body (prose Markdown, cited with
    [^n] markers) plus the numbered sources it cites.
    """

    subtopic_title: str
    body: str
    sources: list[SourceCitation] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slugify(text: str, *, max_len: int = SLUG_MAX_LEN) -> str:
    """Title-Case-Hyphenated, filesystem-safe, bounded to `max_len` chars.

    'How to write Spark Jobs?' -> 'How-To-Write-Spark-Jobs'. Every
    alphanumeric run becomes one hyphen-joined, capitalised word; anything
    else (punctuation, whitespace) is a separator and disappears. Output is
    truncated on a word boundary -- never mid-word, never a trailing hyphen
    -- except that a single word longer than `max_len` is itself hard-cut,
    since there is no boundary left to break on.
    """
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words:
        return "Untitled"
    parts = [w.capitalize() for w in words]
    out = parts[0][:max_len]
    for w in parts[1:]:
        if len(out) + 1 + len(w) > max_len:
            break
        out = f"{out}-{w}"
    return out


def wikilink(slug: str, title: str) -> str:
    """`[[<file-slug>|<Title>]]` -- the only link form this module emits.
    Never `[[<Title>]]`: a bare title link does not resolve to a
    hyphenated filename and would render as a broken link once merged
    into the wiki.
    """
    return f"[[{slug}|{title}]]"


def _dedupe_slug(base_slug: str, used: set[str]) -> str:
    """Two subtopics can slugify to the same string (e.g. two titles that
    differ only in punctuation). Keep filenames -- and therefore
    wikilinks -- unique and stable by appending -2, -3, ... on collision.
    """
    if base_slug not in used:
        used.add(base_slug)
        return base_slug
    n = 2
    while f"{base_slug}-{n}" in used:
        n += 1
    slug = f"{base_slug}-{n}"
    used.add(slug)
    return slug


def render_frontmatter(
    *, title: str, research_topic: str, research_slug: str, research_depth: int,
    research_generated: str, research_model: str, research_engine: str,
    extra: dict | None = None,
) -> str:
    """The frontmatter contract every generated note carries. Deliberately
    does not accept (and must never be passed) a top-level `source` key --
    notes cite sources with a bare `source:` key in body footnotes, and a
    duplicate top-level key fails the whole Quartz build.

    `research_engine` is the engine ID (e.g. "gptr"), not its display label:
    this is the value a Dataview query filters on, and an id can't be
    reworded out from under a saved query the way a label can. It sits
    directly after `research_model` so the two provenance keys stay together.
    """
    data = {
        "title": title,
        "research_topic": research_topic,
        "research_slug": research_slug,
        "research_depth": research_depth,
        "research_generated": research_generated,
        "research_model": research_model,
        "research_engine": research_engine,
        "merged_topic": "Agent-Research",
        "merged_from": "agent-research",
    }
    if extra:
        data.update(extra)
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"---\n{dumped}\ntags: [agent-research, generated]\n---"


def render_callout(topic: str, generated_at: str, n_sources: int, *, engine_label: str) -> str:
    plural = "s" if n_sources != 1 else ""
    return (
        "> [!info] Machine-written\n"
        f"> Generated by researchd using **{engine_label}** from {n_sources} "
        f"source{plural}, researching **{topic}**, on {generated_at}. Verify "
        "before treating as authoritative."
    )


def render_footnotes(sources: list[SourceCitation]) -> str:
    """One footnote definition per source, in numeric order. An excerpt is
    blockquoted with attribution to its source; the model's explanation of
    why it matters sits as its own paragraph *below* the quote, never
    merged into it -- so a reader can always tell where the source's words
    end and the model's commentary begins.
    """
    # NOTE-LOCAL numbering, deliberately not src.index.
    #
    # src.index is a job-wide counter, but the synthesise prompt shows this
    # note's sources renumbered from 1 (llm.py: "[{i + 1}] {title}"), so the
    # model writes [^1], [^2], [^3] no matter where those sources sit in the
    # job. Emitting definitions as [^13], [^14] therefore produced markers and
    # definitions that never matched -- every footnote in every note rendered
    # as a dangling reference. Observed in the first successful job.
    #
    # The fix has to be here rather than in the prompt: renumbering the
    # definitions is deterministic, whereas asking the model to use arbitrary
    # global numbers is one more thing it can get wrong. Sources.md keeps the
    # job-wide numbering, which is what it is for.
    blocks = []
    for position, src in enumerate(sources, start=1):
        lines = [f"[^{position}]: [{src.title}]({src.url})"]
        for ex in src.excerpts:
            lines.append("")
            lines.append(f"    > {ex.text}")
            if ex.note:
                lines.append("")
                lines.append(f"    {ex.note}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_note(
    *, title: str, research_topic: str, research_slug: str, research_depth: int,
    research_generated: str, research_model: str, body: str, sources: list[SourceCitation],
) -> str:
    fm = render_frontmatter(
        title=title, research_topic=research_topic, research_slug=research_slug,
        research_depth=research_depth, research_generated=research_generated,
        research_model=research_model,
    )
    callout = render_callout(research_topic, research_generated, len(sources))
    parts = [fm, "", callout, "", body.strip()]
    footnotes = render_footnotes(sources)
    if footnotes:
        parts += ["", "---", "", footnotes]
    return "\n".join(parts).rstrip() + "\n"


def render_simple_list_note(
    *, title: str, research_topic: str, research_slug: str, research_depth: int,
    research_generated: str, research_model: str, intro: str, items: list[str],
) -> str:
    """Related-Topics.md / Open-Questions.md: a plain bullet list of
    strings the synthesise call proposed. These strings are validated as
    plain text in models.py and are never treated as anything but bullet
    text here -- in particular a related-topic string is never written as
    a link, because it is a search query, not a resolved document.
    """
    fm = render_frontmatter(
        title=title, research_topic=research_topic, research_slug=research_slug,
        research_depth=research_depth, research_generated=research_generated,
        research_model=research_model,
    )
    lines = [fm, "", intro, ""]
    lines += [f"- {item}" for item in items]
    return "\n".join(lines).rstrip() + "\n"


def render_sources_note(
    *, topic: str, slug: str, depth: int, model_id: str, generated_at: str,
    sources: list[SourceCitation],
) -> str:
    fm = render_frontmatter(
        title="Sources", research_topic=topic, research_slug=slug,
        research_depth=depth, research_generated=generated_at, research_model=model_id,
    )
    lines = [fm, "", f"Full bibliography for {wikilink(slug, topic)}.", ""]
    for src in sources:
        doi_part = f" · doi:{src.doi}" if src.doi else ""
        lines.append(f"- [{src.title}]({src.url}){doi_part}")
    return "\n".join(lines).rstrip() + "\n"


def render_moc(
    *, topic: str, slug: str, depth: int, model_id: str, generated_at: str,
    note_slugs: list[tuple[NoteSpec, str]], has_related: bool, has_open_questions: bool,
    n_sources: int, history: list[str],
) -> str:
    extra = {"research_generated_history": history} if history else None
    fm = render_frontmatter(
        title=topic, research_topic=topic, research_slug=slug, research_depth=depth,
        research_generated=generated_at, research_model=model_id, extra=extra,
    )
    callout = render_callout(topic, generated_at, n_sources)
    lines = [
        fm, "", callout, "",
        f"Map of content for **{topic}**, researched at depth {depth}.",
        "", "## Notes", "",
    ]
    for note, note_slug in note_slugs:
        lines.append(f"- {wikilink(note_slug, note.subtopic_title)}")
    lines += ["", "## Sources", "", f"- {wikilink(SOURCES_SLUG, 'Full bibliography')}"]

    further = []
    if has_related:
        further.append((RELATED_TOPICS_SLUG, "Related Topics"))
    if has_open_questions:
        further.append((OPEN_QUESTIONS_SLUG, "Open Questions"))
    if further:
        lines += ["", "## Further"]
        lines += [f"- {wikilink(s, t)}" for s, t in further]

    return "\n".join(lines).rstrip() + "\n"


def _read_prior_history(dir_path: Path, slug: str) -> list[str]:
    """Read the previous run's timestamp(s) off the old MOC before it is
    replaced, so a re-run appends to a `research_generated_history`
    rather than losing when the topic was last researched. Best-effort:
    any read/parse failure just means the history starts fresh, never a
    hard error blocking the re-run.
    """
    moc_path = dir_path / f"{slug}.md"
    if not moc_path.exists():
        return []
    try:
        raw = moc_path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.startswith("---"):
        return []
    end = raw.find("\n---", 3)
    if end == -1:
        return []
    try:
        fm = yaml.safe_load(raw[3:end]) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(fm, dict):
        return []
    history = list(fm.get("research_generated_history") or [])
    prior = fm.get("research_generated")
    if prior:
        history.append(prior)
    seen: set[str] = set()
    out = []
    for h in history:
        if isinstance(h, str) and h not in seen:
            seen.add(h)
            out.append(h)
    return out[-_MAX_HISTORY:]


def write_tree(
    base_dir: Path, *, topic: str, slug: str, depth: int, model_id: str,
    notes: list[NoteSpec], all_sources: list[SourceCitation],
    related_topics: list[str] | None = None, open_questions: list[str] | None = None,
) -> Path:
    """Write the full note tree for `topic` under `base_dir/<slug>/`.

    `slug` is decided by the caller (pipeline.py), after planning, from the
    model's short title rather than re-derived here from `topic` -- so the
    directory name matches the slug already committed to the job row in the
    db. `topic` is still used for frontmatter and prose.

    Re-running an existing topic replaces its directory wholesale -- never
    `-2` duplicates -- and the MOC's `research_generated_history` picks up
    the previous run's timestamp(s) before the old directory is removed.
    """
    dir_path = Path(base_dir) / slug
    generated_at = now_iso()

    history = _read_prior_history(dir_path, slug)

    if dir_path.exists():
        shutil.rmtree(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)

    used_slugs: set[str] = set()
    note_slugs: list[tuple[NoteSpec, str]] = []
    for note in notes:
        note_slug = _dedupe_slug(slugify(note.subtopic_title), used_slugs)
        note_slugs.append((note, note_slug))

    for note, note_slug in note_slugs:
        content = render_note(
            title=note.subtopic_title, research_topic=topic, research_slug=slug,
            research_depth=depth, research_generated=generated_at, research_model=model_id,
            body=note.body, sources=note.sources,
        )
        (dir_path / f"{note_slug}.md").write_text(content, encoding="utf-8")

    moc_content = render_moc(
        topic=topic, slug=slug, depth=depth, model_id=model_id, generated_at=generated_at,
        note_slugs=note_slugs, has_related=bool(related_topics),
        has_open_questions=bool(open_questions), n_sources=len(all_sources), history=history,
    )
    (dir_path / f"{slug}.md").write_text(moc_content, encoding="utf-8")

    sources_content = render_sources_note(
        topic=topic, slug=slug, depth=depth, model_id=model_id, generated_at=generated_at,
        sources=all_sources,
    )
    (dir_path / f"{SOURCES_SLUG}.md").write_text(sources_content, encoding="utf-8")

    if related_topics:
        content = render_simple_list_note(
            title="Related Topics", research_topic=topic, research_slug=slug,
            research_depth=depth, research_generated=generated_at, research_model=model_id,
            intro=f"Topics {wikilink(slug, topic)} suggests for further research.",
            items=related_topics,
        )
        (dir_path / f"{RELATED_TOPICS_SLUG}.md").write_text(content, encoding="utf-8")

    if open_questions:
        content = render_simple_list_note(
            title="Open Questions", research_topic=topic, research_slug=slug,
            research_depth=depth, research_generated=generated_at, research_model=model_id,
            intro=f"Questions {wikilink(slug, topic)} leaves unanswered.",
            items=open_questions,
        )
        (dir_path / f"{OPEN_QUESTIONS_SLUG}.md").write_text(content, encoding="utf-8")

    return dir_path
