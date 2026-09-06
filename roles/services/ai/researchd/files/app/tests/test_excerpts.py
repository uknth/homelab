"""These are the tests that back the pipeline's central trust claim: a
model can point at text with integer offsets, but it can never inject text
of its own as a "quotation". Every rejection case here is a shape a
hallucinating or prompt-injected model could plausibly produce.
"""

from researchd.excerpts import MAX_EXCERPT_CHARS, extract_and_verify, normalise_whitespace

DOC = (
    "The quick brown fox jumps over the lazy dog. "
    "It was a bright cold day in April, and the clocks were striking thirteen."
)


def test_valid_offsets_are_accepted_and_return_the_real_slice():
    start = DOC.index("bright cold day")
    end = start + len("bright cold day")
    assert extract_and_verify(DOC, start, end) == "bright cold day"


def test_accepted_excerpt_is_always_a_literal_substring_of_the_document():
    start = DOC.index("lazy dog")
    end = start + len("lazy dog")
    result = extract_and_verify(DOC, start, end)
    assert result is not None
    assert result in DOC


def test_whitespace_normalisation_tolerates_incidental_whitespace():
    text = "line one\n\n   line two"
    result = extract_and_verify(text, 0, len(text))
    assert result == text
    assert normalise_whitespace(result) == "line one line two"


# ---- rejection path: every one of these is a plausible hallucinated or
# fabricated offset pair, and every one must be dropped silently (None),
# never raise, never partially accepted. ---------------------------------


def test_reversed_range_is_dropped():
    assert extract_and_verify(DOC, 40, 10) is None


def test_negative_start_is_dropped():
    assert extract_and_verify(DOC, -5, 10) is None


def test_zero_length_range_is_dropped():
    assert extract_and_verify(DOC, 5, 5) is None


def test_end_past_document_length_is_dropped():
    assert extract_and_verify(DOC, 0, len(DOC) + 500) is None


def test_start_past_document_length_is_dropped():
    assert extract_and_verify(DOC, len(DOC) + 10, len(DOC) + 20) is None


def test_oversized_excerpt_is_dropped():
    long_doc = "word " * (MAX_EXCERPT_CHARS)
    assert extract_and_verify(long_doc, 0, MAX_EXCERPT_CHARS + 100) is None


def test_whitespace_only_slice_is_dropped():
    doc = "word1        word2"
    assert extract_and_verify(doc, 5, 13) is None


def test_non_integer_offsets_are_dropped():
    assert extract_and_verify(DOC, "10", 20) is None
    assert extract_and_verify(DOC, 10.5, 20) is None
    assert extract_and_verify(DOC, None, 20) is None
    assert extract_and_verify(DOC, 10, None) is None
    assert extract_and_verify(DOC, [10], 20) is None


def test_boolean_offsets_are_dropped():
    # bool is an int subclass in Python; a model returning JSON true/false
    # must not be silently coerced into offsets 0/1.
    assert extract_and_verify(DOC, True, 20) is None
    assert extract_and_verify(DOC, 0, True) is None


def test_a_fabricated_excerpt_never_reaches_the_document():
    # The security property in one assertion: no matter what offsets a
    # model supplies, the only text extract_and_verify can ever return is
    # a literal slice of the real document -- there is no parameter, no
    # code path, through which the model's own wording could be returned
    # instead. Exhaustively checking every accepted excerpt across a
    # spread of offsets makes that structural guarantee concrete.
    fabricated = "the moon is made of green cheese, according to this source"
    for start in range(0, len(DOC), 7):
        for end in range(start + 1, min(start + 40, len(DOC) + 1)):
            result = extract_and_verify(DOC, start, end)
            if result is not None:
                assert result != fabricated
                assert result in DOC
