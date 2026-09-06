"""The pydantic schemas in models.py are the other half of the
schema-validate-or-discard contract (excerpts.py is the half that matters
once an offset is trusted). These tests check that malformed LLM output --
missing fields, wrong types, empty results -- fails validation rather than
being coerced into something usable.
"""

import pytest
from pydantic import ValidationError

from researchd.models import (
    ExcerptOffset,
    PlanOutline,
    ResearchRequest,
    SummariseResult,
    SynthesiseOutcome,
)


def test_plan_outline_accepts_well_formed_data():
    outline = PlanOutline.model_validate({
        "subtopics": [
            {"title": "Spark Execution Model", "scope": "how jobs run",
             "search_queries": ["spark execution model", "spark DAG scheduler"]},
        ],
    })
    assert len(outline.subtopics) == 1
    assert outline.subtopics[0].title == "Spark Execution Model"


def test_plan_outline_rejects_empty_subtopics():
    with pytest.raises(ValidationError):
        PlanOutline.model_validate({"subtopics": []})


def test_plan_outline_rejects_missing_subtopics_key():
    with pytest.raises(ValidationError):
        PlanOutline.model_validate({"outline": "not the right shape"})


def test_plan_subtopic_search_queries_are_capped_and_cleaned():
    outline = PlanOutline.model_validate({
        "subtopics": [{
            "title": "X",
            "search_queries": ["a", "", "  ", "b", "c", "d", "e", "f", "g"],
        }],
    })
    queries = outline.subtopics[0].search_queries
    assert "" not in queries
    assert len(queries) <= 6


def test_summarise_result_requires_a_non_empty_summary():
    with pytest.raises(ValidationError):
        SummariseResult.model_validate({"summary": "   ", "excerpts": []})


def test_summarise_result_excerpts_are_offset_pairs_not_text():
    result = SummariseResult.model_validate({
        "summary": "A summary of the document.",
        "excerpts": [{"start": 10, "end": 20, "note": "why it matters"}],
    })
    assert isinstance(result.excerpts[0], ExcerptOffset)
    assert result.excerpts[0].start == 10
    assert result.excerpts[0].end == 20
    # There is no field here that could carry the model's own quoted text.
    assert not hasattr(result.excerpts[0], "text")


def test_summarise_result_rejects_string_offsets():
    with pytest.raises(ValidationError):
        SummariseResult.model_validate({
            "summary": "ok",
            "excerpts": [{"start": "not-a-number", "end": 20}],
        })


def test_summarise_result_caps_excerpt_count():
    result = SummariseResult.model_validate({
        "summary": "ok",
        "excerpts": [{"start": i, "end": i + 5} for i in range(0, 100, 10)],
    })
    assert len(result.excerpts) <= 5


def test_synthesise_outcome_defaults_are_empty_lists():
    outcome = SynthesiseOutcome.model_validate({"body": "prose"})
    assert outcome.related_topics == []
    assert outcome.open_questions == []


def test_synthesise_outcome_related_topics_are_capped_and_cleaned():
    outcome = SynthesiseOutcome.model_validate({
        "body": "prose",
        "related_topics": ["a", "", "  ", *[f"topic-{i}" for i in range(20)]],
    })
    assert "" not in outcome.related_topics
    assert len(outcome.related_topics) <= 10


def test_research_request_requires_topic_or_url():
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate({"depth": 2})


def test_research_request_rejects_out_of_range_depth():
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate({"topic": "x", "depth": 6})
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate({"topic": "x", "depth": 0})


def test_research_request_accepts_default_depth():
    req = ResearchRequest.model_validate({"topic": "Spark"})
    assert req.depth == 2
