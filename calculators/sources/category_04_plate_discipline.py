"""Network fetchers that feed the Category 4 plate-discipline calculators.

Category 4 is Statcast pitch-level, like Category 3, and for the same reason: the
MLB Stats API publishes no per-pitch location, no per-pitch count state, and no
swing/take/whiff description. Nothing new is fetched. These are thin projections
over `_fetch_season_statcast`, the season-scoped, cache-backed puller Category 2
introduced, so a Category 2, 3, or 4 pull of the same player and season shares a
single network call. The cache stores the raw frame and field projection happens
afterward, which is why each category may project its own field tuple.

Both sides of the matchup are required. Every Category 4 calculator pairs a
hitter's discipline rate with the pitcher rate that exploits or blunts it, so the
fetchers come in a batter/pitcher pair like Category 3's.
"""

from __future__ import annotations

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
)

# Pitch fields the Category 4 calculators read.
#
# `zone` is the load-bearing one, and it is used as published rather than
# reconstructed from `plate_x`/`plate_z` against `sz_top`/`sz_bot`. Those four
# columns are deliberately absent from this tuple: a coordinate reconstruction
# was tried and disagreed with Statcast's own `zone` on 52 of 1,210 tracked
# pitches (4.3 percent, 2026-08-09), because the published zone accounts for the
# ball's radius and for a per-batter strike zone that the plate-half-width
# constant does not. Reading a column nobody uses would also make this tuple a
# claim about the category that is not true.
#
# Codes 1 through 9 are the 3x3 in-zone grid, numbered left to right and top to
# bottom from the catcher's view, which is exactly the grid CALC_30 wants. Codes
# 11 through 14 are the four out-of-zone quadrants. There is no code 10.
#
# `zone` is null on pitches with no tracking data, and not only on the obvious
# ones: the probe frames carried 19 `automatic_ball` rows (pitch-timer
# violations, where no pitch was thrown) but also 3 `called_strike`, 2 `foul`,
# and 2 `hit_into_play` rows with a null zone. Every zone-based denominator here
# therefore counts pitches *carrying a zone reading*, never all pitches.
#
# `balls` and `strikes` are the count **before** the pitch, verified on the probe
# frames: every `pitch_number == 1` row carried (0, 0). CALC_28's first-pitch
# filter and CALC_29's two-strike filter both depend on that reading.
CATEGORY_04_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "events",
    "description",
    "type",
    "zone",
    "balls",
    "strikes",
    "estimated_ba_using_speedangle",
)


def fetch_batter_discipline(batter_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw in *season*.

    Establishes the hitter's side of every Category 4 matchup: what he swings at
    in the zone and out of it (CALC_24, CALC_25), how often he misses (CALC_26,
    CALC_27), how he opens a plate appearance (CALC_28), how he protects with two
    strikes (CALC_29), and where in the zone he does damage (CALC_30).

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("batter", batter_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast discipline fetch failed for batter {batter_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_04_PITCH_FIELDS)


def fetch_pitcher_discipline(pitcher_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *pitcher_id* threw in *season*.

    Establishes the starter's side: how much of the zone he fills (CALC_24,
    CALC_25), how many swings he misses (CALC_26, CALC_27), how often he steals
    strike one (CALC_28), how well he closes with two strikes (CALC_29), and the
    location distribution his hitter-side zone profile gets weighted by
    (CALC_30).

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("pitcher", pitcher_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast discipline fetch failed for pitcher {pitcher_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_04_PITCH_FIELDS)
