"""Assemble every calculator input for one hitter-game and run the whole model.

This is the only place that knows how to go from "this hitter, in this lineup,
facing this starter, in this park" to the merged mapping of all 108 calculator
keys. `main.py` uses it for the `Model` column and
`scripts/evaluate_sample_coverage.py` uses it to measure coverage, so the two
cannot drift apart.

Cost
----
`assemble` issues roughly a dozen MLB Stats API requests and needs **two Statcast
frames**, the hitter's season and the opposing starter's. Those frames are cached
on disk per player per season per day, so the first evaluation of a player on a
given day pays for the pull (measured at roughly 78 seconds) and every later one
is nearly free (measured at 1.9 seconds for a full 108-key evaluation).

That asymmetry is why `main.py` does not compute the model on `--scheduled` runs
by default. A fifty-hitter slate is fifty batter frames plus up to thirty starter
frames, which is over an hour on the first tick of the day, against a fifteen
minute cron cadence. See issue #50.

Bullpens are out of scope
-------------------------
Per the user, 2026-08-09, this tool ranks hitters against traditional starting
pitchers. Nothing here fetches Category 7, and `main.py` filters opener matchups
out before this is ever called.
"""

from __future__ import annotations

import datetime
import statistics
from typing import Any

from calculators import baselines
from calculators.category_01_bvp_matchups import compute_category_01
from calculators.category_02_platoon_splits import compute_category_02
from calculators.category_03_pitch_arsenal import compute_category_03
from calculators.category_04_plate_discipline import compute_category_04
from calculators.category_05_ballpark_environment import compute_category_05
from calculators.category_06_lineup_game_context import compute_category_06
from calculators.category_08_batter_form import compute_category_08
from calculators.category_09_pitcher_form import compute_category_09
from calculators.category_10_defense_schedule import compute_category_10
from calculators.category_11_composite import (
    aggregate_hit_rate,
    calc_76_game_hit_probability,
    compute_category_11,
    projected_plate_appearances,
)
from calculators.sources import category_01_bvp_matchups as src01
from calculators.sources import category_02_platoon_splits as src02
from calculators.sources import category_03_pitch_arsenal as src03
from calculators.sources import category_04_plate_discipline as src04
from calculators.sources import category_05_ballpark_environment as src05
from calculators.sources import category_06_lineup_game_context as src06
from calculators.sources import category_08_batter_form as src08
from calculators.sources import category_09_pitcher_form as src09
from calculators.sources import category_10_defense_schedule as src10


def resolve_arm_angle(pitcher_pitches: list[dict]) -> float | None:
    """Mean arm angle over the starter's tracked pitches, or None.

    Read off Category 2's projection, which carries `arm_angle`. Category 3's
    does not, and reading that one instead returns None for every pitcher, which
    makes `CALC_14` look unreachable when it is merely unprojected.
    """
    angles = [
        float(p["arm_angle"]) for p in pitcher_pitches if p.get("arm_angle") is not None
    ]
    return statistics.fmean(angles) if angles else None


def merge_environment(raw_game: dict, game_pk: int, is_home: bool | None) -> dict:
    """Schedule-derived environment, with any boxscore readings layered on top.

    The boxscore half is merged **under**, not over: `fetch_game_environment`
    returns the full key set with None for anything it does not carry, so a plain
    `dict.update` overwrites the schedule half with nulls and silently empties
    `CALC_37` and `CALC_38`.

    *raw_game* must be a **hydrated** schedule record. An unhydrated one carries
    no venue and no team abbreviation, which costs ten keys per hitter; see
    issue #43.
    """
    environment = src05.environment_from_schedule(raw_game, is_home)
    for key, value in src05.fetch_game_environment(game_pk).items():
        if value is not None:
            environment[key] = value
    return environment


def assemble(
    batter_id: int,
    pitcher_id: int | None,
    *,
    raw_game: dict,
    game_pk: int,
    team_id: int | None = None,
    lineup_side: list[dict] | None = None,
    lineup_spot: int | None = None,
    is_home: bool | None = None,
    game_number: int = 1,
    start_time: datetime.datetime | None = None,
    season: int | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Fetch every input and return the merged mapping of all calculator keys.

    Absent optional inputs resolve their dependent keys to None rather than
    raising, so a caller with a partial picture still gets everything the rest of
    the data supports.
    """
    today = today or datetime.date.today()
    season = season or today.year

    hands = src02.fetch_handedness([i for i in (batter_id, pitcher_id) if i])
    # `fetch_handedness` keys these "bats" and "throws". Reading them as
    # "bat_side"/"pitch_hand" silently empties all of Category 2.
    bat_side = (hands.get(batter_id) or {}).get("bats")
    throws = (hands.get(pitcher_id) or {}).get("throws") if pitcher_id else None

    batter_pitches = src02.fetch_batter_statcast(batter_id, season)
    pitcher_pitches = (
        src02.fetch_pitcher_statcast(pitcher_id, season) if pitcher_id else []
    )
    arsenal = src03.fetch_pitcher_arsenal(pitcher_id, season) if pitcher_id else []
    batter_form = src08.fetch_batter_form(batter_id, season)

    bvp = (
        src01.fetch_bvp_stats(batter_id, pitcher_id)
        if pitcher_id
        else src01.empty_bvp()
    )
    results: dict[str, Any] = {}
    results.update(
        compute_category_01(
            bvp,
            season,
            src01.fetch_bvp_statcast(batter_id, pitcher_id, season)
            if pitcher_id
            else [],
        )
    )
    results.update(
        compute_category_02(
            hitter_splits=src02.fetch_stat_splits(batter_id, "hitting", season),
            pitcher_splits=(
                src02.fetch_stat_splits(pitcher_id, "pitching", season)
                if pitcher_id
                else {}
            ),
            hitter_pitches=batter_pitches,
            pitcher_pitches=pitcher_pitches,
            batter_side=bat_side,
            pitcher_throws=throws,
            starter_arm_angle=resolve_arm_angle(pitcher_pitches),
            league_baseline=baselines.load_league_platoon_baseline(),
            today=today,
        )
    )
    results.update(
        compute_category_03(src03.fetch_batter_arsenal(batter_id, season), arsenal)
    )
    results.update(
        compute_category_04(
            src04.fetch_batter_discipline(batter_id, season),
            src04.fetch_pitcher_discipline(pitcher_id, season) if pitcher_id else [],
        )
    )

    environment = merge_environment(raw_game, game_pk, is_home)
    results.update(
        compute_category_05(
            environment=environment,
            ballparks=baselines.load_ballparks(),
            park_factors=baselines.load_park_factors(),
            batter_pitches=src05.fetch_batter_spray(batter_id, season),
            batter_splits=src05.fetch_situational_splits(batter_id, "hitting", season),
            pitcher_splits=(
                src05.fetch_situational_splits(pitcher_id, "pitching", season)
                if pitcher_id
                else {}
            ),
            bat_side=bat_side,
        )
    )

    side = lineup_side or []
    results.update(
        compute_category_06(
            lineup_spot=lineup_spot,
            lineup=src06.lineup_by_spot(
                side, src06.fetch_lineup_rates([p["id"] for p in side], season)
            )
            if side
            else {},
            pitcher_pitches=(
                src06.fetch_pitcher_tto(pitcher_id, season) if pitcher_id else []
            ),
            batter_pitches=src06.fetch_batter_tto(batter_id, season),
            batter_is_home=is_home,
            game_context=baselines.load_league_game_context(),
        )
    )
    results.update(compute_category_08(batter_form, today))
    results.update(
        compute_category_09(
            src09.fetch_pitcher_form(pitcher_id, season) if pitcher_id else [], today
        )
    )
    results.update(
        compute_category_10(
            game={
                "game_pk": game_pk,
                "game_date": today.isoformat(),
                "game_number": game_number,
                "day_night": environment.get("day_night"),
                "venue_id": environment.get("venue_id"),
                "start_time": start_time,
            },
            game_log=src10.fetch_team_game_log(team_id, today) if team_id else [],
            ballparks=baselines.load_ballparks(),
            bvp=bvp,
        )
    )
    results.update(
        compute_category_11(
            pitches=batter_form,
            bvp=results.get("CALC_01"),
            platoon=results.get("CALC_09"),
            pitch_type=results.get("CALC_20"),
            lineup_spot_pa=results.get("CALC_41"),
            ninth_inning_risk=results.get("CALC_45"),
            today=today,
        )
    )
    return results


def model_probability(results: dict[str, Any]) -> float | None:
    """The `Model` column: P(at least one hit) from the broad aggregate.

    `aggregate_hit_rate` pools every calculator reporting hits per plate
    appearance, then `CALC_76` turns that into a per-game probability using the
    Category 6 projection. Read `aggregate_hit_rate`'s docstring before trusting
    the number: it over-weights the season baseline by construction, and it has
    never been validated against outcomes (#35).

    Returns None when either the aggregate or the plate-appearance projection is
    missing, so a caller can distinguish "no model answer" from a low one.
    """
    aggregate = aggregate_hit_rate(results)
    projected = projected_plate_appearances(
        results.get("CALC_41"), results.get("CALC_45")
    )
    game = calc_76_game_hit_probability(aggregate, projected)
    return game.value.rate if game is not None and game.value is not None else None
