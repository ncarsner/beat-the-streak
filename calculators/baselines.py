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
BALLPARKS_PATH = pathlib.Path("calculators/data/ballparks.json")
PARK_FACTORS_PATH = pathlib.Path("calculators/data/park_factors.json")
LEAGUE_GAME_CONTEXT_PATH = pathlib.Path("calculators/data/league_game_context.json")

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


def _load_venue_table(path: pathlib.Path, label: str) -> dict:
    """Return the ``venues`` mapping of a generated venue table, or {}.

    Shared by the two Category 5 tables, which have the same envelope: provenance
    keys at the top level and a ``venues`` mapping keyed by MLB venue id as a
    string. A missing or malformed file returns ``{}`` rather than raising, and
    the calculators that read it then return None, which is the honest answer
    when the reference they measure against is absent.

    Unlike the platoon baseline this does **not** check for completeness. Both
    tables are legitimately partial: Savant's park-factor leaderboard carried 29
    of 30 venues on 2026-08-09 (Sutter Health Park has too little history for a
    three-year window), and its roof-closed grouping covers only the 8 parks that
    have a roof. A per-venue lookup that misses returns None on its own, which is
    correct, where rejecting the whole table would discard 29 good venues to
    punish one gap.
    """
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{label} unavailable ({exc})")
        return {}

    venues = document.get("venues")
    if not isinstance(venues, dict):
        print(f"{label} malformed: no venues mapping")
        return {}
    return venues


def load_ballparks(path: pathlib.Path | None = None) -> dict:
    """Return the static ballpark geometry table keyed by MLB venue id.

    ``{"3313": {"name": ..., "elevation_ft": 55, "azimuth_degrees": 75.0,
    "roof_type": "Open", "turf_type": "Grass", "fences_ft": [318, 399, 408, 385,
    314]}, ...}``. Regenerate with ``scripts/generate_ballparks.py``.
    """
    return _load_venue_table(path or BALLPARKS_PATH, "Ballpark table")


def load_league_game_context(path: pathlib.Path | None = None) -> dict:
    """Return the league game-context constants `CALC_45` measures against.

    ``{"skipped_ninth": {"rate": .4429, "denominator": 1761, "skipped": 780},
    "pa_lost_per_skipped_ninth": 0.5, ...}``. Regenerate with
    ``scripts/generate_league_game_context.py``.

    Unlike every other league-reference constant in this repo, this one is a
    genuine league census rather than a convenience sample: it comes from the MLB
    Stats API's schedule, so it does not inherit issue #38's broken bulk Statcast
    pull.

    A missing or malformed file returns ``{}``, and `CALC_45` then returns None
    for a home hitter, which is the honest answer when the rate it scales by is
    absent.
    """
    path = path or LEAGUE_GAME_CONTEXT_PATH
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"League game context unavailable ({exc})")
        return {}
    if not isinstance(document, dict) or "skipped_ninth" not in document:
        print("League game context malformed: no skipped_ninth")
        return {}
    return document


def load_park_factors(path: pathlib.Path | None = None) -> dict:
    """Return the park-factor table keyed by MLB venue id.

    ``{"2": {"name": ..., "groupings": {"All": {"n_pa": 52026, "index_hits": 104,
    ...}, "L": {...}, "R": {...}, "roof_closed": {...}}}, ...}``, where 100 is
    league-neutral.

    **This is a snapshot of a moving quantity.** The underlying window is
    3-year rolling and includes the in-progress season, so the file's
    ``year_range`` and ``generated`` keys matter; regenerate periodically with
    ``scripts/generate_park_factors.py`` rather than treating it as an annual
    constant.
    """
    return _load_venue_table(path or PARK_FACTORS_PATH, "Park factor table")
