"""Loaders for generated league-reference data.

These files are produced on demand by the scripts in `scripts/` and checked in,
so a calculator reads a league baseline as a constant rather than fetching it.
Nothing here touches the network — that is what keeps the calculators that
depend on a baseline testable without mocking, and what keeps a daily run from
paying for a league-wide aggregation it cannot use.

Values are auditable and diffable in git, which matters more than it sounds:
a baseline is the thing every reverse-split judgement is measured against, and a
silent shift in it moves every CALC_15 output at once.
"""

from __future__ import annotations

import json
import pathlib

LEAGUE_PLATOON_BASELINE_PATH = pathlib.Path(
    "calculators/data/league_platoon_baseline.json"
)

# The four cells a complete platoon baseline must carry, as
# "<batter hand>_vs_<pitcher hand>".
PLATOON_CELLS = ("L_vs_L", "L_vs_R", "R_vs_L", "R_vs_R")


def load_league_platoon_baseline(path: pathlib.Path | None = None) -> dict:
    """Return the league batter-hand x pitcher-hand baseline, or {} if unusable.

    Returns the ``splits`` mapping: ``{"L_vs_R": {"rate": .2518,
    "denominator": 36714, ...}, ...}``.

    A missing, malformed, or incomplete file returns ``{}`` rather than raising,
    matching the fetchers' error contract — a calculator that depends on the
    baseline then returns None, which is the honest answer when the thing it
    compares against is absent. An incomplete file is treated as unusable rather
    than partially used: a 2x2 missing a cell would silently bias whichever
    matchups happened to land in the missing one.
    """
    path = path or LEAGUE_PLATOON_BASELINE_PATH
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"League platoon baseline unavailable ({exc})")
        return {}

    splits = document.get("splits")
    if not isinstance(splits, dict):
        return {}
    if any(cell not in splits for cell in PLATOON_CELLS):
        print(f"League platoon baseline incomplete: {sorted(splits)}")
        return {}
    return splits
