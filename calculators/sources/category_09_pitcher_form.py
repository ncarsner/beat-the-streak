"""Network fetcher that feeds the Category 9 pitcher-form calculators.

The pitcher-side mirror of Category 8: single-sided, one fetcher, and nothing
about the opposing hitter enters. Another thin projection over
`_fetch_season_statcast`, so a Category 9 pull shares its network call with
whatever Categories 2 through 4 already pulled for the same pitcher and season.

**Season-scoped**, which bounds the recency comparisons. `CALC_62` and `CALC_63`
compare recent starts against a season baseline rather than a career one, because
a career baseline needs a multi-season pull that does not exist yet.
"""

from __future__ import annotations

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
)

# Pitch fields the Category 9 calculators read.
#
# Four of these exist only for this category, and each one is load-bearing.
#
# - `inning` and `outs_when_up` together identify a **start** and reconstruct
#   **innings pitched**, neither of which Statcast publishes directly. There is
#   no starter/reliever flag anywhere in the feed. The start test is "the
#   pitcher's earliest plate appearance in the game was in inning 1 with zero
#   outs", which held for 19 of 19 games in the probe frame.
# - `inning_topbot` scopes a half-inning. It is constant across a single
#   pitcher's rows within a game (he pitches one side of it), and the outs
#   reconstruction asserts that rather than assuming it.
# - `bat_score` and `post_bat_score` give runs allowed. The batting team's score
#   is the right pair: the *fielding* team's score says nothing about what this
#   pitcher gave up.
#
# `game_type` carries the same weight it does in Category 8: a season pull
# includes spring training, which was 10.1% of the probe pitcher's rows.
CATEGORY_09_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "inning",
    "inning_topbot",
    "outs_when_up",
    "events",
    "description",
    "pitch_type",
    "release_speed",
    "zone",
    "launch_speed",
    "bat_score",
    "post_bat_score",
)


def fetch_pitcher_form(pitcher_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *pitcher_id* threw in *season*.

    Feeds every Category 9 calculator. The calculators reduce these to starts
    themselves rather than receiving pre-grouped records, because the
    reconstruction of innings pitched needs the raw half-inning structure that a
    per-start summary would have already thrown away.

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("pitcher", pitcher_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast form fetch failed for pitcher {pitcher_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_09_PITCH_FIELDS)
