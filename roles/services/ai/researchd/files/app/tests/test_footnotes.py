"""Footnote markers in a note body must match its footnote definitions.

Regression test for the first successful research job (2026-09-04): bodies
cited [^1][^2][^3] while definitions were emitted as [^13][^14][^15], because
the definitions used a job-wide counter while the synthesise prompt renumbers
each note's sources from 1. Every footnote rendered as a dangling reference.
"""

import re

from researchd.write import SourceCitation, render_footnotes


def _defs(md):
    return sorted({int(m) for m in re.findall(r"\[\^(\d+)\]:", md)})


def test_definitions_are_note_local_not_global():
    sources = [
        SourceCitation(index=13, title="A", url="https://a.example"),
        SourceCitation(index=14, title="B", url="https://b.example"),
        SourceCitation(index=15, title="C", url="https://c.example"),
    ]
    assert _defs(render_footnotes(sources)) == [1, 2, 3]


def test_single_source_is_footnote_one():
    md = render_footnotes([SourceCitation(index=99, title="Z", url="https://z.example")])
    assert _defs(md) == [1]


def test_order_is_preserved():
    sources = [
        SourceCitation(index=7, title="First", url="https://1.example"),
        SourceCitation(index=3, title="Second", url="https://2.example"),
    ]
    md = render_footnotes(sources)
    assert md.index("First") < md.index("Second")
    assert _defs(md) == [1, 2]
