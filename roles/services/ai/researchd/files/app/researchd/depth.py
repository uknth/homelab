"""The depth table, as data. Every number that scales a job with the `1`-`5`
depth dial lives here and only here -- pipeline.py reads a DepthConfig, it
never hardcodes a subtopic count or a source cap inline. Tuning the scale is
a table edit, never a code change (docs/spec/research.md, "Depth").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepthConfig:
    depth: int
    subtopics: int
    sources_per_subtopic: int
    recursion_levels: int
    related_topics_note: bool
    open_questions_note: bool
    prioritise_papers: bool
    related_topic_budget: int  # cap on related-topic search queries per synthesise call


DEPTH_TABLE: dict[int, DepthConfig] = {
    1: DepthConfig(
        depth=1, subtopics=3, sources_per_subtopic=3, recursion_levels=0,
        related_topics_note=False, open_questions_note=False,
        prioritise_papers=False, related_topic_budget=0,
    ),
    2: DepthConfig(
        depth=2, subtopics=5, sources_per_subtopic=5, recursion_levels=0,
        related_topics_note=False, open_questions_note=False,
        prioritise_papers=False, related_topic_budget=0,
    ),
    3: DepthConfig(
        depth=3, subtopics=8, sources_per_subtopic=6, recursion_levels=0,
        related_topics_note=True, open_questions_note=False,
        prioritise_papers=False, related_topic_budget=3,
    ),
    4: DepthConfig(
        depth=4, subtopics=12, sources_per_subtopic=8, recursion_levels=1,
        related_topics_note=True, open_questions_note=False,
        prioritise_papers=False, related_topic_budget=3,
    ),
    5: DepthConfig(
        depth=5, subtopics=18, sources_per_subtopic=10, recursion_levels=2,
        related_topics_note=True, open_questions_note=True,
        prioritise_papers=True, related_topic_budget=4,
    ),
}

DEFAULT_DEPTH = 2
MIN_DEPTH = 1
MAX_DEPTH = 5


def get_depth_config(depth: int) -> DepthConfig:
    if depth not in DEPTH_TABLE:
        raise ValueError(f"invalid depth {depth!r}; must be one of {sorted(DEPTH_TABLE)}")
    return DEPTH_TABLE[depth]
