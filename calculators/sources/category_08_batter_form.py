"""Network fetcher that feeds the Category 8 batter-form calculators.

Category 8 is the first **single-sided** category. Categories 1 through 4 all ask
how a hitter fares against some property of today's starter; Category 8 asks only
how the hitter is going, so there is one fetcher rather than a batter/pitcher
pair. Nothing about the opposing pitcher enters any of `CALC_52`-`CALC_59`.

Another thin projection over `_fetch_season_statcast`, so a Category 8 pull
shares its network call with whatever Categories 1 through 4 already pulled for
the same batter and season.

**Season-scoped, which bounds what this category can answer.** The puller fetches
one season, so `CALC_58`'s "recent versus season *or career* baseline" is served
with a season baseline only. A career BABIP would need a multi-season pull, and a
multi-season pull is a different cache key and a different cost, so it is left
undone rather than faked from one year.
"""

from __future__ import annotations

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
)

# Pitch fields the Category 8 calculators read.
#
# `game_type` is the one to understand before changing this tuple. Category 8 is
# entirely about recency, and a Statcast season pull **includes spring training**:
# 96 of the probe batter's 1,227 pitches (7.8%) and 185 of the probe pitcher's
# 1,831 (10.1%) carried `game_type == "S"`, dated from 2026-02-21. At season
# aggregate that is noise; across a three-game or seven-day window in late March
# it is most of the sample.
#
# It also fixes a second problem at the same time. Statcast **computes no
# expected statistics for spring training**: every one of the probe frame's 13
# spring batted balls carried a null `estimated_ba_using_speedangle`, against 1 of
# 143 in the regular season. Keeping spring games would therefore let plate
# appearances into a denominator whose expected-stat numerator silently vanishes,
# which is exactly the bias `CALC_57` exists to measure. Filtering flipped the
# probe hitter's luck delta from +0.0043 to -0.0183.
#
# `launch_speed` and `launch_angle` are the quality-of-contact pair: hard-hit rate
# for `CALC_55`, sweet-spot rate for `CALC_59`. Both are null on the roughly 1
# batted ball in 150 that Statcast cannot measure, and every reader treats a null
# as no reading rather than as a zero.
CATEGORY_08_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "events",
    "description",
    "launch_speed",
    "launch_angle",
    "estimated_ba_using_speedangle",
    "estimated_woba_using_speedangle",
)


def fetch_batter_form(batter_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw in *season*.

    Feeds every Category 8 calculator. The calculators reduce these to plate
    appearances themselves rather than receiving pre-grouped records, because
    `CALC_59` windows by plate appearance while `CALC_53` windows by calendar day
    and `CALC_52` by game, and the three need the same rows grouped three ways.

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("batter", batter_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast form fetch failed for batter {batter_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_08_PITCH_FIELDS)
