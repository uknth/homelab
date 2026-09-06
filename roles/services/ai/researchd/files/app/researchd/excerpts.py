"""Excerpts are extracted, never generated.

This is the module that makes the pipeline's output trustworthy. The
summarise stage (llm.py) returns *integer character offsets* into the
source text it was shown -- never a quoted string. `extract_and_verify`
below is the only place an excerpt's text is ever materialised, and it
always comes from slicing our own copy of the fetched document, not from
anything the model wrote.

A model cannot fabricate a quotation through an offset: the worst it can
do is point at the wrong place, or hand us a nonsensical pair of numbers.
Both are caught here and the excerpt is dropped silently -- no exception,
no retry, no partial acceptance. Silence matters as much as rejection: a
verbose error would just teach a future prompt-injected page how to shape
its content to slip past the check.
"""

from __future__ import annotations

import re

# An excerpt this long stopped being "a quotation worth citing" a while
# ago; cap it so a wildly wrong offset pair can't smuggle half a document
# into a footnote.
MAX_EXCERPT_CHARS = 1200

_WHITESPACE = re.compile(r"\s+")


def normalise_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def extract_and_verify(document_text: str, start: object, end: object) -> str | None:
    """Slice `document_text[start:end]` and verify the result is real,
    verbatim text (whitespace-normalised) from that same document.

    Returns the excerpt text, or ``None`` if:
      * `start`/`end` are not plain integers (a model can return anything
        as JSON -- floats, strings, booleans, null -- and none of it is a
        valid offset),
      * the range is empty, reversed, negative, or runs past the end of
        the document,
      * the range is larger than a sane excerpt should ever be,
      * the sliced text is empty once whitespace is trimmed,
      * the sliced text -- after whitespace normalisation -- cannot be
        found in the (whitespace-normalised) document at all. This is the
        genuine defence: it catches offsets computed against a different
        piece of text than `document_text` (e.g. a stale chunk, or a
        different source entirely), which is precisely the shape a
        fabricated or mismatched excerpt takes.

    Never raises. Bad model output is data to be discarded, not an
    exception to be handled -- there is no code path here that can turn
    into an action.
    """
    # bool is a subclass of int in Python; reject it explicitly so a model
    # returning `true`/`false` cannot be silently coerced into 0/1.
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 0 or end <= start:
        return None
    if end - start > MAX_EXCERPT_CHARS:
        return None
    if end > len(document_text):
        return None

    candidate = document_text[start:end]
    if not candidate.strip():
        return None

    normalised_candidate = normalise_whitespace(candidate)
    if not normalised_candidate:
        return None

    normalised_document = normalise_whitespace(document_text)
    if normalised_candidate not in normalised_document:
        # Structurally this should be unreachable when start/end index
        # into document_text directly -- it exists to catch the case
        # where a caller passes offsets that were computed against a
        # *different* text than the one being verified against (a stale
        # chunk, a mismatched source, a translation bug). Fail closed.
        return None

    return candidate
