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

Implemented so far: category 1 (batter vs. pitcher), `CALC_01`-`CALC_08`.
Nothing in this package is called during a run yet — the calculators are being
validated individually and will be consumed together by the composite model
(`CALC_75`).

Only the cross-category surface is re-exported here. Calculators themselves are
imported from the module that owns them —
``from calculators.category_01_bvp_matchups import calc_01_bvp_career_hit_rate``
— so eleven categories cannot collide in one flat namespace, and a
category-specific constant stays where it belongs.
"""

from calculators.common import (
    COUNTING_STATS,
    Rate,
    aggregate_lines,
    empty_line,
    rate_or_none,
)

__all__ = [
    "COUNTING_STATS",
    "Rate",
    "aggregate_lines",
    "empty_line",
    "rate_or_none",
]
