"""Network fetchers that feed the Category 3 pitch-arsenal calculators.

Category 3 is entirely Statcast pitch-level: every one of `CALC_16`-`CALC_23`
reads columns the MLB Stats API does not expose at all (`pitch_type`,
`release_extension`, `delta_run_exp`, `pfx_x`, the release-velocity vector). No
new dependency and no new endpoint — these are thin projections over
`_fetch_season_statcast`, the same season-scoped, cache-backed puller Category 2
uses.

Because that cache is keyed ``(role, player_id, season)`` and stores the *raw*
frame, with field projection happening afterward, a Category 2 fetch and a
Category 3 fetch of the same player and season share a single network call —
whichever category runs first pays for it. That is why these fetchers project a
different field tuple rather than widening `CATEGORY_02_PITCH_FIELDS`: the
column set a category reads is a property of the category, not of the cache.

Both sides of the matchup are required. Unlike Category 2, where each calculator
draws from one side, every Category 3 calculator is a genuine cross-product: the
*pitcher's* rows establish what he throws (arsenal mix, velocity, extension,
approach angle), and the *hitter's* rows establish how he fares against that
class of pitch. See the calculator module for the per-calculator split.
"""

from __future__ import annotations

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
)

# Pitch fields the Category 3 calculators read.
#
# Two of these are easy to omit and fail silently rather than loudly:
#
# - `pitch_number` is what identifies the *terminal* pitch of a plate
#   appearance. Category 3 filters on attributes that vary within a PA
#   (velocity, pitch type, break), so it must attribute the PA's outcome to the
#   pitch that ended it. Without this column there is nothing to rank on.
# - `ay` is the y-axis acceleration term in the vertical-approach-angle
#   solution. Drop it and `vertical_approach_angle` returns None for every
#   pitch, which reads as "no Statcast coverage" rather than as a bug.
#
# `attack_angle` is bat-tracking data: the column exists from 2023 but is
# entirely null that season, populated from 2024, and only ever on pitches the
# batter actually swung at. `_normalize_pitches` skips columns absent from the
# frame rather than fabricating them, so a pre-2023 pull simply omits the key
# and every reader treats it as a missing reading. Nothing in this category
# takes its primary value from it — see `CALC_21`.
CATEGORY_03_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "events",
    "description",
    "pitch_type",
    "release_speed",
    "effective_speed",
    "release_extension",
    "delta_run_exp",
    "pfx_x",
    "vy0",
    "ay",
    "vz0",
    "az",
    "attack_angle",
    "estimated_ba_using_speedangle",
)


def fetch_pitcher_arsenal(pitcher_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *pitcher_id* threw in *season*.

    Establishes the starter's side of every Category 3 matchup: what he throws
    and how much of it (`CALC_16`-`CALC_19`, `CALC_22`), how hard
    (`CALC_20`), at what approach angle (`CALC_21`), and from how far down the
    mound (`CALC_23`).

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("pitcher", pitcher_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast arsenal fetch failed for pitcher {pitcher_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_03_PITCH_FIELDS)


def fetch_batter_arsenal(batter_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw in *season*.

    Establishes the hitter's side: his expected-batting-average and run value
    against each pitch class, and his hit rate against the velocity, approach
    angle, break, and extension tiers today's starter occupies.

    Deliberately not filtered to one opposing pitcher — Category 3 asks how a
    hitter fares against a *class* of pitch, which is answerable even when he
    has never faced this particular starter. Shares the "batter" cache role with
    `fetch_bvp_statcast` and `fetch_batter_statcast`, so a run that already
    pulled this batter's season pays no second call.
    """
    try:
        frame = _fetch_season_statcast("batter", batter_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast arsenal fetch failed for batter {batter_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_03_PITCH_FIELDS)
