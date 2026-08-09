"""Network fetchers that feed the Category 5 ballpark and environment calculators.

Three inputs, from three different places, and the split is the point:

1. **Static venue geometry and park factors** come from neither of the fetchers
   here. They are checked-in tables loaded by `calculators.baselines`, generated
   on demand by `scripts/generate_ballparks.py` and
   `scripts/generate_park_factors.py`. A run pays no network cost for them.
2. **Game environment** (`fetch_game_environment`) reads the boxscore, the same
   endpoint `main.fetch_lineup` already calls.
3. **Situational splits** (`fetch_situational_splits`) reuse Category 2's
   `fetch_stat_splits` with the h/a/d/n codes, one request per player.

Spray data for `CALC_33` and `CALC_36`, and venue history for `CALC_40`, come
from `fetch_batter_spray`, another thin projection over Category 2's
`_fetch_season_statcast`, so it shares its network call with whatever Categories
1 through 4 and 8 already pulled for the same batter and season.

Weather and wind are published on a later clock than the lineup
--------------------------------------------------------------
**Measured 2026-08-09 and load-bearing for anyone who wires this up.** The
boxscore's `Weather`, `Wind`, and `officials` fields are populated reliably after
a game but only sporadically before it: of 8 Preview-state games that morning,
2 carried weather and 6 did not, and it did not track start time (three games at
the same 17:35Z first pitch split 1 populated, 2 not).

Two consequences. `CALC_34`, `CALC_35`, `CALC_36`, and `CALC_39` are fully usable
for the backtest in issue #35, where every game is complete and the fields are
always there, and unreliable at live pick time, where they resolve to None more
often than not. That is expressible in the return type rather than hidden, so it
is not a blocker.

And when this is wired in, it needs the `refresh_opposing_pitchers` treatment.
`main.py`'s `queried_games_cache` short-circuits the boxscore fetch for the rest
of the day once a lineup is found, so a field that publishes *later* than the
lineup would be frozen at its first-fetch value: a run at 10am would cache a
missing weather reading and never see the one that appeared at 6pm. Probable
pitchers already had this exact shape.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import requests

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
    empty_stat_splits,
    fetch_stat_splits,
)
from mlb_api import MLB_API_BASE

# Situation codes CALC_37 and CALC_38 read: home, away, day, night. All four
# arrive in one response, so the pair of calculators costs one request per
# player, not two.
SITUATIONAL_SIT_CODES = ("h", "a", "d", "n")

# Boxscore `info` labels carrying the environment. The values are prose
# ("89 degrees, Partly Cloudy.", "11 mph, Out To RF.") and are passed through
# unparsed: parsing them is pure string work and belongs with the calculators,
# which keeps it testable without mocking a request.
WEATHER_LABEL = "Weather"
WIND_LABEL = "Wind"

# Pitch fields the Category 5 calculators read.
#
# `hc_x` / `hc_y` are the batted-ball landing coordinates `CALC_33` and
# `CALC_36` solve a spray angle from, and `bb_type` is what restricts that to
# balls hit in the air. `home_team` is how a Statcast row identifies where a game
# was played, since there is no venue column, and it is the whole basis of
# `CALC_40`. `game_type` carries the spring-training filter every recency-aware
# category applies (issue #42).
CATEGORY_05_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "events",
    "description",
    "bb_type",
    "hc_x",
    "hc_y",
    "home_team",
    "away_team",
)


def empty_environment() -> dict[str, Any]:
    """What a failed or absent boxscore lookup normalizes to.

    Every key present and None, rather than an empty dict, so
    `compute_category_05` can read the record without guarding each field and a
    missing environment resolves each calculator to None on its own terms.
    """
    return {
        "venue_id": None,
        "weather": None,
        "wind": None,
        "day_night": None,
        "home_team": None,
        "batter_is_home": None,
    }


def _info_values(payload: dict) -> dict[str, str]:
    """Map the boxscore `info` list of label/value pairs into a dict."""
    return {
        item.get("label"): item.get("value")
        for item in payload.get("info") or []
        if item.get("label")
    }


def fetch_game_environment(game_pk: int) -> dict[str, Any]:
    """Return the weather and wind strings for *game_pk*, or a None-filled record.

    Issues one GET /game/{game_pk}/boxscore. That is the same request
    `main.fetch_lineup` makes, so wiring this into a run costs nothing extra --
    subject to the publish-clock caveat in the module docstring, which is the
    reason this is a separate function rather than a field added to the lineup
    fetch.

    The remaining environment keys (`venue_id`, `day_night`, `home_team`,
    `batter_is_home`) come from the schedule record rather than the boxscore; see
    `environment_from_schedule`.

    Returns a None-filled record on request failure rather than raising, matching
    the error contract of every other fetcher in this package.
    """
    record = empty_environment()
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/game/{game_pk}/boxscore",
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Boxscore environment fetch error for game {game_pk} ({exc})")
        return record

    info = _info_values(resp.json())
    record["weather"] = info.get(WEATHER_LABEL)
    record["wind"] = info.get(WIND_LABEL)
    return record


def environment_from_schedule(
    game: dict[str, Any],
    batter_is_home: bool | None = None,
) -> dict[str, Any]:
    """Project a schedule record onto the environment keys it can supply.

    `venue_id` and `day_night` are on the schedule record `main.fetch_schedule`
    already pulls, and unlike weather they are published as soon as the game is
    scheduled. Kept separate from `fetch_game_environment` for exactly that
    reason: these are reliable at pick time and the weather half is not, so a
    caller can have `CALC_31`, `CALC_32`, `CALC_37`, and `CALC_38` even on a day
    when the boxscore carries no conditions yet.

    Pure, no network. *game* is the raw MLB Stats API schedule game object.
    """
    record = empty_environment()
    record["venue_id"] = (game.get("venue") or {}).get("id")
    record["day_night"] = game.get("dayNight")
    home = ((game.get("teams") or {}).get("home") or {}).get("team") or {}
    record["home_team"] = home.get("abbreviation")
    record["batter_is_home"] = batter_is_home
    return record


def fetch_situational_splits(
    player_id: int,
    group: Literal["hitting", "pitching"],
    season: int | None = None,
) -> dict[str, dict[str, int]]:
    """Return home/away/day/night statSplits for *player_id* in *season*.

    One request covering all four codes, for either stat group. Delegates to
    Category 2's `fetch_stat_splits` rather than re-implementing the call: that
    function already normalizes a pitching split's `battersFaced` onto
    `plateAppearances`, without which `CALC_37` and `CALC_38` would return None
    for every pitcher.

    Returns an empty mapping on failure, which resolves both calculators to None.
    """
    season = season or date.today().year
    try:
        return fetch_stat_splits(player_id, group, season, SITUATIONAL_SIT_CODES)
    except Exception as exc:  # noqa: BLE001 - matches the fetcher error contract
        print(f"Situational splits fetch failed for player {player_id} ({exc})")
        return empty_stat_splits()


def fetch_batter_spray(batter_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw in *season*.

    Feeds `CALC_33`, `CALC_36`, and `CALC_40`. The calculators reduce these to
    plate appearances and then to batted balls themselves, rather than receiving
    pre-grouped records, because `CALC_40` counts every plate appearance at a
    venue while the other two count only balls hit in the air.

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("batter", batter_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast spray fetch failed for batter {batter_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_05_PITCH_FIELDS)
