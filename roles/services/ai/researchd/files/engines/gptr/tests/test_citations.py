"""citations.py is the one piece of pure logic doing real work in this
sidecar: turning gpt-researcher's own inline-hyperlink citation style into
researchd's note-local [^n] footnote contract. Covered directly, with no
gpt-researcher involved at all.
"""

from __future__ import annotations

from gptr.citations import footnote_sources


def test_parenthesized_citation_becomes_a_footnote_marker():
    body = "Spark runs jobs as a DAG of stages ([Spark Docs](https://spark.apache.org/docs))."
    new_body, sources = footnote_sources(body, {"https://spark.apache.org/docs": "Spark Docs"})

    assert new_body == "Spark runs jobs as a DAG of stages [^1]."
    assert sources == [
        {"index": 1, "title": "Spark Docs", "url": "https://spark.apache.org/docs", "doi": None, "excerpts": []},
    ]


def test_bare_link_citation_also_becomes_a_footnote_marker():
    body = "See [the paper](https://arxiv.org/abs/1234) for details."
    new_body, sources = footnote_sources(body, {})

    assert new_body == "See [^1] for details."
    assert sources[0]["title"] == "the paper"  # falls back to link text, no known title


def test_repeated_citation_of_the_same_url_reuses_one_index():
    body = (
        "First claim ([A](https://a.example))."
        " Second claim, same source ([A](https://a.example))."
    )
    new_body, sources = footnote_sources(body, {"https://a.example": "A"})

    assert new_body.count("[^1]") == 2
    assert len(sources) == 1


def test_numbering_is_first_appearance_order_not_url_order():
    body = "([Z](https://z.example)) then ([A](https://a.example))."
    _, sources = footnote_sources(body, {})

    assert [s["url"] for s in sources] == ["https://z.example", "https://a.example"]
    assert [s["index"] for s in sources] == [1, 2]


def test_no_citations_yields_untouched_body_and_no_sources():
    body = "Plain prose with no citations at all."
    new_body, sources = footnote_sources(body, {})

    assert new_body == body
    assert sources == []


def test_title_falls_back_to_url_when_no_title_and_no_link_text():
    body = "Reference: ([](https://bare.example))."
    _, sources = footnote_sources(body, {})

    assert sources[0]["title"] == "https://bare.example"


def test_trailing_references_section_is_stripped_before_footnoting():
    body = (
        "Body text with a citation ([X](https://x.example)).\n"
        "\n"
        "## References\n"
        "\n"
        "[https://x.example](https://x.example)\n"
        "[https://y.example](https://y.example)\n"
    )
    new_body, sources = footnote_sources(body, {"https://x.example": "X"})

    assert "References" not in new_body
    assert "y.example" not in new_body
    # Only the in-body citation is numbered; the reference-list-only URL
    # (never cited in the prose itself) never gets an entry.
    assert [s["url"] for s in sources] == ["https://x.example"]
