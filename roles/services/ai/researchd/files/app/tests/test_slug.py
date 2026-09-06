import re

from researchd.write import slugify


def test_slug_matches_the_spec_example():
    assert slugify("How to write Spark Jobs?") == "How-To-Write-Spark-Jobs"


def test_slug_strips_punctuation():
    assert slugify("C++ vs. Rust: A Comparison!") == "C-Vs-Rust-A-Comparison"


def test_slug_collapses_whitespace_and_symbol_separators():
    assert slugify("  multiple   spaces_and-dashes  ") == "Multiple-Spaces-And-Dashes"


def test_slug_normalises_case():
    assert slugify("PARTITIONING and shuffles") == "Partitioning-And-Shuffles"


def test_slug_handles_apostrophes_as_separators():
    assert slugify("NASA's JWST Discoveries") == "Nasa-S-Jwst-Discoveries"


def test_slug_falls_back_to_untitled_for_no_alnum_content():
    assert slugify("???") == "Untitled"
    assert slugify("") == "Untitled"
    assert slugify("   ") == "Untitled"


def test_slug_is_deterministic():
    text = "Partitioning and Shuffles"
    assert slugify(text) == slugify(text) == "Partitioning-And-Shuffles"


def test_slug_is_filesystem_safe():
    slug = slugify("What/is..this? <weird> \\ topic")
    assert "/" not in slug and "\\" not in slug
    assert all(c.isalnum() or c == "-" for c in slug)


def test_slug_a_single_massive_word_is_hard_cut_to_the_cap():
    # No word boundary to break on, so it is hard-cut -- the one case where
    # truncation is allowed to land mid-word.
    slug = slugify("a" * 100)
    assert slug == "A" + "a" * 59
    assert len(slug) == 60


def test_slug_caps_length_on_a_word_boundary_for_a_real_long_topic():
    # Regression test for the deadlock: research-pull.sh on cmp01 rejects
    # any slug over 64 chars (research-pull.sh.j2:67), which stranded a job
    # at `ready` forever because n8n never called /published for it.
    topic = (
        "Why most women are not inclined towards technical jobs or "
        "succeeding in a technical field?"
    )
    slug = slugify(topic)
    assert len(slug) <= 60
    assert re.match(r"^[A-Za-z0-9_-]{1,64}$", slug)
    assert not slug.endswith("-")
    last_word = slug.rsplit("-", 1)[-1]
    assert last_word.lower() in {w.lower() for w in re.findall(r"[A-Za-z0-9]+", topic)}


def test_slug_max_len_override_is_respected():
    slug = slugify("Partitioning and Shuffles and Execution Model Details", max_len=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")
