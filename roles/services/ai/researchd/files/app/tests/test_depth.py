import pytest

from researchd.depth import DEFAULT_DEPTH, DEPTH_TABLE, get_depth_config


def test_default_depth_is_two():
    assert DEFAULT_DEPTH == 2


def test_depth_table_matches_spec_numbers():
    # subtopics, sources_per_subtopic, recursion_levels -- straight out of
    # the table in docs/spec/research.md.
    expected = {
        1: (3, 3, 0),
        2: (5, 5, 0),
        3: (8, 6, 0),
        4: (12, 8, 1),
        5: (18, 10, 2),
    }
    for depth, (subtopics, sources, recursion) in expected.items():
        cfg = DEPTH_TABLE[depth]
        assert cfg.subtopics == subtopics
        assert cfg.sources_per_subtopic == sources
        assert cfg.recursion_levels == recursion


def test_extras_switch_on_at_the_right_depth():
    assert [DEPTH_TABLE[d].related_topics_note for d in range(1, 6)] == \
        [False, False, True, True, True]
    assert [DEPTH_TABLE[d].open_questions_note for d in range(1, 6)] == \
        [False, False, False, False, True]
    assert [DEPTH_TABLE[d].prioritise_papers for d in range(1, 6)] == \
        [False, False, False, False, True]


def test_get_depth_config_returns_the_table_entry():
    for d in range(1, 6):
        assert get_depth_config(d) is DEPTH_TABLE[d]


def test_invalid_depth_raises_value_error():
    with pytest.raises(ValueError):
        get_depth_config(0)
    with pytest.raises(ValueError):
        get_depth_config(6)
    with pytest.raises(ValueError):
        get_depth_config(-1)
