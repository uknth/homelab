"""Regression guard for the vaultmerge integration bug: every wikilink
this module emits must resolve to a file that actually exists in the tree
it just wrote, using the `[[slug|Title]]` alias form -- never a bare
`[[Title]]`, which does not resolve against a hyphenated filename.
"""

from researchd.write import WIKILINK_RE, NoteSpec, SourceCitation, slugify, write_tree


def _notes():
    shared_source = SourceCitation(
        index=1, title="Spark Docs", url="https://spark.apache.org/docs",
    )
    return [
        NoteSpec(
            subtopic_title="Spark Execution Model",
            body="Some body text with a citation. [^1]",
            sources=[shared_source],
        ),
        NoteSpec(
            subtopic_title="Partitioning & Shuffles!",
            body="More body text, also cited. [^1]",
            sources=[shared_source],
        ),
    ]


def test_every_wikilink_resolves_to_a_real_file(tmp_path):
    notes = _notes()
    all_sources = [c for n in notes for c in n.sources]
    dir_path = write_tree(
        tmp_path, topic="How to write Spark Jobs?", slug=slugify("How to write Spark Jobs?"),
        depth=2, model_id="test-model", notes=notes, all_sources=all_sources,
    )

    existing_stems = {p.stem for p in dir_path.glob("*.md")}
    assert existing_stems

    checked_any = False
    for md_file in dir_path.glob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        for slug, _title in WIKILINK_RE.findall(text):
            checked_any = True
            assert slug in existing_stems, (
                f"{md_file.name} links to [[{slug}|...]], which does not "
                f"resolve to any file among {sorted(existing_stems)}"
            )
    assert checked_any  # sanity: the MOC actually contains wikilinks to check


def test_moc_never_uses_the_bare_title_link_form(tmp_path):
    notes = _notes()
    all_sources = [c for n in notes for c in n.sources]
    dir_path = write_tree(
        tmp_path, topic="How to write Spark Jobs?", slug=slugify("How to write Spark Jobs?"),
        depth=2, model_id="test-model", notes=notes, all_sources=all_sources,
    )
    moc = (dir_path / "How-To-Write-Spark-Jobs.md").read_text(encoding="utf-8")
    assert "[[Spark Execution Model]]" not in moc
    assert "[[Spark-Execution-Model|Spark Execution Model]]" in moc


def test_related_topics_and_open_questions_are_linked_from_the_moc(tmp_path):
    notes = _notes()
    all_sources = [c for n in notes for c in n.sources]
    dir_path = write_tree(
        tmp_path, topic="How to write Spark Jobs?", slug=slugify("How to write Spark Jobs?"),
        depth=5, model_id="test-model", notes=notes, all_sources=all_sources,
        related_topics=["structured streaming checkpoints"],
        open_questions=["how does AQE interact with dynamic allocation?"],
    )
    existing_stems = {p.stem for p in dir_path.glob("*.md")}
    assert "Related-Topics" in existing_stems
    assert "Open-Questions" in existing_stems

    moc = (dir_path / "How-To-Write-Spark-Jobs.md").read_text(encoding="utf-8")
    for slug, _title in WIKILINK_RE.findall(moc):
        assert slug in existing_stems


def test_note_filenames_are_deterministic_and_rerun_replaces_in_place(tmp_path):
    notes = _notes()
    all_sources = [c for n in notes for c in n.sources]
    first = write_tree(tmp_path, topic="How to write Spark Jobs?",
                        slug=slugify("How to write Spark Jobs?"), depth=2, model_id="m",
                        notes=notes, all_sources=all_sources)
    first_files = sorted(p.name for p in first.glob("*.md"))

    second = write_tree(tmp_path, topic="How to write Spark Jobs?",
                         slug=slugify("How to write Spark Jobs?"), depth=2, model_id="m",
                         notes=notes, all_sources=all_sources)
    second_files = sorted(p.name for p in second.glob("*.md"))

    assert first == second  # same directory -- replaced in place, never "-2"
    assert first_files == second_files


def test_duplicate_subtopic_titles_get_unique_stable_slugs(tmp_path):
    dup_notes = [
        NoteSpec(subtopic_title="Overview", body="First.", sources=[]),
        NoteSpec(subtopic_title="Overview", body="Second.", sources=[]),
    ]
    dir_path = write_tree(
        tmp_path, topic="Duplicate Titles Test", slug=slugify("Duplicate Titles Test"),
        depth=1, model_id="m", notes=dup_notes, all_sources=[],
    )
    stems = {p.stem for p in dir_path.glob("*.md")}
    assert "Overview" in stems
    assert "Overview-2" in stems

    moc = (dir_path / "Duplicate-Titles-Test.md").read_text(encoding="utf-8")
    linked_slugs = {slug for slug, _title in WIKILINK_RE.findall(moc)}
    assert "Overview" in linked_slugs
    assert "Overview-2" in linked_slugs
