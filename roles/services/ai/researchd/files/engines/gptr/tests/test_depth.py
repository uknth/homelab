import pytest

from gptr.depth import DEPTH_TABLE, get_depth_config


def test_depth_table_matches_researchd_s_subtopic_and_source_counts():
    # docs/spec/research.md's table: 1=3/3, 2=5/5, 3=8/6, 4=12/8, 5=18/10.
    expected = {1: (3, 3), 2: (5, 5), 3: (8, 6), 4: (12, 8), 5: (18, 10)}
    for depth, (subtopics, sources) in expected.items():
        cfg = DEPTH_TABLE[depth]
        assert (cfg.subtopics, cfg.max_search_results_per_query) == (subtopics, sources)


def test_max_iterations_scales_monotonically_with_depth():
    values = [DEPTH_TABLE[d].max_iterations for d in range(1, 6)]
    assert values == sorted(values)


def test_get_depth_config_rejects_out_of_range_depth():
    with pytest.raises(ValueError):
        get_depth_config(0)
    with pytest.raises(ValueError):
        get_depth_config(6)


def test_get_depth_config_returns_the_matching_row():
    cfg = get_depth_config(3)
    assert cfg.depth == 3
    assert cfg.subtopics == 8
