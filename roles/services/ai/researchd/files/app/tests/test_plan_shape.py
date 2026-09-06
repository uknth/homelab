"""The plan stage must tolerate the shape the model actually returns.

Regression test for a live failure on the first real job (2026-09-04): the
model was asked for {"subtopics": [...]} and returned the bare array, so
PlanOutline.model_validate() raised and the job died at planning with
"the model did not return a usable outline".

Coercing the container is safe; coercing a *value* would not be. These tests
pin both halves of that: a bare list is accepted, and junk inside it is still
rejected.
"""

import pytest
from pydantic import ValidationError

from researchd.models import PlanOutline


def _coerce(data):
    """Mirrors the coercion in llm.plan()."""
    if isinstance(data, list):
        data = {"subtopics": data}
    return PlanOutline.model_validate(data)


def test_bare_list_is_accepted():
    outline = _coerce([
        {"title": "Setting up Spark", "scope": "basics", "search_queries": ["spark setup"]},
        {"title": "Partitioning and Shuffles"},
    ])
    assert [s.title for s in outline.subtopics] == [
        "Setting up Spark", "Partitioning and Shuffles",
    ]


def test_wrapped_object_still_accepted():
    outline = _coerce({"subtopics": [{"title": "Execution Model"}]})
    assert outline.subtopics[0].title == "Execution Model"


def test_subtopic_without_queries_keeps_empty_list():
    # pipeline falls back to [title] when this is empty; it must not be None.
    outline = _coerce([{"title": "Testing And Deployment"}])
    assert outline.subtopics[0].search_queries == []


def test_empty_list_is_still_rejected():
    with pytest.raises(ValidationError):
        _coerce([])


def test_garbage_elements_are_still_rejected():
    # Coercion changes the container only — element validation is untouched.
    with pytest.raises(ValidationError):
        _coerce(["just a string", 42])


def test_title_absent_defaults_to_none():
    # A model that omits the title must not fail the whole plan -- the
    # caller (pipeline.py) falls back to the raw topic when this is None.
    outline = _coerce([{"title": "Execution Model"}])
    assert outline.title is None


def test_title_present_is_preserved():
    outline = PlanOutline.model_validate(
        {"title": "Spark Internals", "subtopics": [{"title": "Execution Model"}]}
    )
    assert outline.title == "Spark Internals"
