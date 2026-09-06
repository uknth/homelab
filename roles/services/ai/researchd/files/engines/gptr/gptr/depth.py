"""The depth table, as data -- same discipline as researchd's own depth.py:
every number the `1`-`5` dial controls lives here, engine.py only ever reads
a DepthConfig, never hardcodes a subtopic count or a search-result cap inline.

The three fields map onto gpt-researcher's own knobs, set as env vars for the
duration of one job (see engine.py's `_apply_depth_env`):

  * `subtopics`                  -> a hard cap on gpt-researcher's returned
                                    subtopic list (also nudges MAX_SUBTOPICS,
                                    but that only steers the planning prompt;
                                    the model can and does ignore it, so the
                                    cap is enforced again in engine.py).
  * `max_search_results_per_query` -> MAX_SEARCH_RESULTS_PER_QUERY, gpt-
                                    researcher's own per-query result cap.
  * `max_iterations`             -> MAX_ITERATIONS, gpt-researcher's own cap
                                    on search-query planning rounds per
                                    researcher instance (main + each
                                    subtopic). Not part of researchd's own
                                    depth table (which has no equivalent
                                    knob) -- these values are this sidecar's
                                    own judgment call, scaled the same
                                    direction as the other two.

`subtopics`/`max_search_results_per_query` deliberately match researchd's
own DEPTH_TABLE (docs/spec/research.md, "Depth") 1:1 so a topic researched at
the same depth costs roughly the same either engine handles it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepthConfig:
    depth: int
    subtopics: int
    max_search_results_per_query: int
    max_iterations: int


DEPTH_TABLE: dict[int, DepthConfig] = {
    1: DepthConfig(depth=1, subtopics=3, max_search_results_per_query=3, max_iterations=1),
    2: DepthConfig(depth=2, subtopics=5, max_search_results_per_query=5, max_iterations=1),
    3: DepthConfig(depth=3, subtopics=8, max_search_results_per_query=6, max_iterations=2),
    4: DepthConfig(depth=4, subtopics=12, max_search_results_per_query=8, max_iterations=2),
    5: DepthConfig(depth=5, subtopics=18, max_search_results_per_query=10, max_iterations=3),
}

MIN_DEPTH = 1
MAX_DEPTH = 5


def get_depth_config(depth: int) -> DepthConfig:
    if depth not in DEPTH_TABLE:
        raise ValueError(f"invalid depth {depth!r}; must be one of {sorted(DEPTH_TABLE)}")
    return DEPTH_TABLE[depth]
