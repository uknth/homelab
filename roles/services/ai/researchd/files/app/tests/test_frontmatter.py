import yaml

from researchd.write import render_frontmatter


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
    )
    data = _parse(fm)
    assert data == {
        "title": "Partitioning and Shuffles",
        "research_topic": "How to write Spark Jobs",
        "research_slug": "How-To-Write-Spark-Jobs",
        "research_depth": 2,
        "research_generated": "2026-09-03T14:32:11+05:30",
        "research_model": "Qwen3-30B-A3B-Instruct-2507-4bit",
        "merged_topic": "Agent-Research",
        "merged_from": "agent-research",
        "tags": ["agent-research", "generated"],
    }


def test_frontmatter_tags_are_flow_style_as_in_the_spec_example():
    fm = render_frontmatter(
        title="X", research_topic="Y", research_slug="Y", research_depth=1,
        research_generated="2026-01-01T00:00:00+00:00", research_model="m",
    )
    assert "tags: [agent-research, generated]" in fm


def test_frontmatter_never_emits_a_top_level_source_key():
    # notes cite sources with a bare `source:` key in body footnotes; a
    # duplicate top-level key would break the whole Quartz build.
    fm = render_frontmatter(
        title="X", research_topic="Y", research_slug="Y", research_depth=1,
        research_generated="2026-01-01T00:00:00+00:00", research_model="m",
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
        extra={"research_generated_history": ["2026-01-01T00:00:00+00:00"]},
    )
    data = _parse(fm)
    assert data["research_generated_history"] == ["2026-01-01T00:00:00+00:00"]
    assert data["research_generated"] == "2026-01-02T00:00:00+00:00"
