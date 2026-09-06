import yaml

from researchd.write import (
    NoteSpec,
    SourceCitation,
    render_callout,
    render_frontmatter,
    write_tree,
)


def _parse(fm_text: str) -> dict:
    assert fm_text.startswith("---\n")
    body = fm_text[4:]
    end = body.rindex("\n---")
    return yaml.safe_load(body[:end])


def test_frontmatter_matches_the_documented_contract():
    fm = render_frontmatter(
        title="Partitioning and Shuffles",
        research_topic="How to write Spark Jobs",
        research_slug="How-To-Write-Spark-Jobs",
        research_depth=2,
        research_generated="2026-09-03T14:32:11+05:30",
        research_model="Qwen3-30B-A3B-Instruct-2507-4bit",
        research_engine="gptr",
    )
    data = _parse(fm)
    assert data == {
        "title": "Partitioning and Shuffles",
        "research_topic": "How to write Spark Jobs",
        "research_slug": "How-To-Write-Spark-Jobs",
        "research_depth": 2,
        "research_generated": "2026-09-03T14:32:11+05:30",
        "research_model": "Qwen3-30B-A3B-Instruct-2507-4bit",
        "research_engine": "gptr",
        "merged_topic": "Agent-Research",
        "merged_from": "agent-research",
        "tags": ["agent-research", "generated"],
    }


def test_frontmatter_engine_key_sits_directly_after_model_key():
    # A Dataview query filters on the id, and a human skimming raw
    # frontmatter expects the two provenance keys to sit together.
    fm = render_frontmatter(
        title="X", research_topic="Y", research_slug="Y", research_depth=1,
        research_generated="2026-01-01T00:00:00+00:00", research_model="m",
        research_engine="native",
    )
    body = fm[4:fm.rindex("\n---")]
    keys = [
        line.split(":", 1)[0] for line in body.splitlines()
        if line and not line.startswith((" ", "-"))
    ]
    assert keys.index("research_engine") == keys.index("research_model") + 1


def test_frontmatter_tags_are_flow_style_as_in_the_spec_example():
    fm = render_frontmatter(
        title="X", research_topic="Y", research_slug="Y", research_depth=1,
        research_generated="2026-01-01T00:00:00+00:00", research_model="m",
        research_engine="native",
    )
    assert "tags: [agent-research, generated]" in fm


def test_frontmatter_never_emits_a_top_level_source_key():
    # notes cite sources with a bare `source:` key in body footnotes; a
    # duplicate top-level key would break the whole Quartz build.
    fm = render_frontmatter(
        title="X", research_topic="Y", research_slug="Y", research_depth=1,
        research_generated="2026-01-01T00:00:00+00:00", research_model="m",
        research_engine="native",
    )
    data = _parse(fm)
    assert "source" not in data
    assert "source" not in fm.splitlines()[0]


def test_frontmatter_has_no_duplicate_keys_even_with_special_characters_in_title():
    fm = render_frontmatter(
        title="Kubernetes: what it is, and isn't",
        research_topic="Kubernetes: what it is, and isn't",
        research_slug="Kubernetes-What-It-Is-And-Isn-T",
        research_depth=3,
        research_generated="2026-01-01T00:00:00+00:00",
        research_model="m",
        research_engine="native",
    )
    body = fm[4:fm.rindex("\n---")]
    top_level_keys = [
        line.split(":", 1)[0] for line in body.splitlines()
        if line and not line.startswith((" ", "-"))
    ]
    assert len(top_level_keys) == len(set(top_level_keys))
    data = _parse(fm)
    assert data["title"] == "Kubernetes: what it is, and isn't"


def test_frontmatter_extra_keys_are_additive_only():
    fm = render_frontmatter(
        title="X", research_topic="Y", research_slug="Y", research_depth=1,
        research_generated="2026-01-02T00:00:00+00:00", research_model="m",
        research_engine="native",
        extra={"research_generated_history": ["2026-01-01T00:00:00+00:00"]},
    )
    data = _parse(fm)
    assert data["research_generated_history"] == ["2026-01-01T00:00:00+00:00"]
    assert data["research_generated"] == "2026-01-02T00:00:00+00:00"


def test_render_callout_names_the_engine_label():
    callout = render_callout("Spark", "2026-01-01T00:00:00+00:00", 3, engine_label="GPT Researcher")
    assert "**GPT Researcher**" in callout
    assert "3 sources" in callout


def _tree_notes():
    return [
        NoteSpec(
            subtopic_title="Spark Execution Model",
            body="Body text with a citation. [^1]",
            sources=[SourceCitation(index=1, title="Spark Docs", url="https://spark.apache.org/docs")],
        ),
    ]


def test_write_tree_stamps_research_engine_on_every_file_it_writes(tmp_path):
    notes = _tree_notes()
    all_sources = [c for n in notes for c in n.sources]
    dir_path = write_tree(
        tmp_path, topic="How to write Spark Jobs?", slug="How-To-Write-Spark-Jobs",
        depth=1, model_id="m", engine="gptr", engine_label="GPT Researcher",
        notes=notes, all_sources=all_sources,
        related_topics=["structured streaming checkpoints"],
        open_questions=["how does AQE interact with dynamic allocation?"],
    )

    # frontmatter carries research_engine on every note kind write_tree
    # produces -- not just the MOC, per the spec's "all pages generated".
    expected_files = {
        "How-To-Write-Spark-Jobs.md",  # MOC
        "Spark-Execution-Model.md",  # subtopic note
        "Sources.md",
        "Related-Topics.md",
        "Open-Questions.md",
    }
    for name in expected_files:
        path = dir_path / name
        assert path.exists(), f"expected {name} to be written"
        text = path.read_text(encoding="utf-8")
        fm = _parse(text[: text.index("\n---", 4) + 4])
        assert fm["research_engine"] == "gptr", f"{name} missing research_engine=gptr"

    # only the MOC and subtopic notes render the "Machine-written" callout;
    # those two must name the engine label visibly, not just in frontmatter.
    for name in ("How-To-Write-Spark-Jobs.md", "Spark-Execution-Model.md"):
        text = (dir_path / name).read_text(encoding="utf-8")
        assert "**GPT Researcher**" in text, f"{name} callout missing engine label"
