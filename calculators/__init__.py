"""Probability calculators for the hit-probability model.

One module per category of `ROADMAP.md`'s 76 considerations, so a calculator
lives with the others that share its data source and its reason for existing.

Layout and naming, applied to every category as it lands:

- ``category_NN_<slug>.py`` — the pure calculators for ROADMAP category ``NN``,
  where ``<slug>`` is the category's title in snake_case. No network I/O.
- ``calc_NN_<slug>(...)`` — one function per numbered ROADMAP consideration,
  named for the ``CALC_NN`` it implements. Returns a `Rate` or ``None``.
- ``compute_category_NN(...)`` — runs every implemented calculator in that
  module and returns ``{"CALC_NN": Rate | None, ...}``.
- ``common.py`` — the types and counting-stat helpers shared across categories.
- ``sources.py`` — the only module here that touches the network. Pure
  calculator modules never import it, so the arithmetic stays testable without
  mocking.

Implemented so far: category 1 (batter vs. pitcher). Nothing in this package is
called during a run yet — the calculators are being validated individually and
will be consumed together by the composite model (`CALC_75`).
"""

from calculators.common import COUNTING_STATS, Rate, aggregate_lines, empty_line
from calculators.category_01_bvp_matchups import (
    RECENT_WINDOW_YEARS,
    calc_01_bvp_career_hit_rate,
    calc_02_bvp_season_hit_rate,
    calc_03_bvp_recent_window_hit_rate,
    calc_04_bvp_contact_rate,
    compute_category_01,
    parse_bvp_stats,
)

__all__ = [
    "COUNTING_STATS",
    "RECENT_WINDOW_YEARS",
    "Rate",
    "aggregate_lines",
    "calc_01_bvp_career_hit_rate",
    "calc_02_bvp_season_hit_rate",
    "calc_03_bvp_recent_window_hit_rate",
    "calc_04_bvp_contact_rate",
    "compute_category_01",
    "empty_line",
    "parse_bvp_stats",
]
