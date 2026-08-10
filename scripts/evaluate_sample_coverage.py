"""Measure how many calculator values actually resolve for the ROADMAP sample batters.

Runs every implemented calculator against today's posted lineups for the batters
in `ROADMAP.md`'s sample list, and reports how many keys come back with a usable
value rather than `None`.

**This measures coverage, not quality.** A calculator that resolves for all ten
sample hitters may still carry no predictive signal whatsoever. Nothing here says
any of these numbers help; that is issue #35, of which this is only the first
half.

Every `None` is classified, because they are not the same finding:

  structural     the calculator has no reachable source at all and returns None
                 by design. Expected, and not a coverage gap.
  empty sample   the input was supplied and contained nothing relevant. Real
                 coverage information: this pair has never faced each other, or
                 this hitter has no batted ball in that tier yet.
  missing input  this harness did not supply the argument. A defect in this
                 script rather than a fact about the model, and the number worth
                 driving to zero.

Usage::

    PYTHONPATH=. python3 scripts/evaluate_sample_coverage.py [--date YYYY-MM-DD]

Costs roughly two Statcast pulls per hitter (his own frame and his opposing
starter's), cached on disk under `.cache/statcast`, so a second run is nearly
free. Each hitter is evaluated inside a try/except so one failure leaves the
other results intact.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import traceback
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from calculators import baselines  # noqa: E402
from calculators.category_01_bvp_matchups import compute_category_01  # noqa: E402
from calculators.category_02_platoon_splits import compute_category_02  # noqa: E402
from calculators.category_03_pitch_arsenal import compute_category_03  # noqa: E402
from calculators.category_04_plate_discipline import compute_category_04  # noqa: E402
from calculators.category_05_ballpark_environment import (  # noqa: E402
    compute_category_05,
)
from calculators.category_06_lineup_game_context import (  # noqa: E402
    compute_category_06,
)
from calculators.category_08_batter_form import compute_category_08  # noqa: E402
from calculators.category_09_pitcher_form import compute_category_09  # noqa: E402
from calculators.category_10_defense_schedule import compute_category_10  # noqa: E402
from calculators.category_11_composite import compute_category_11  # noqa: E402
from calculators.sources import category_01_bvp_matchups as src01  # noqa: E402
from calculators.sources import category_02_platoon_splits as src02  # noqa: E402
from calculators.sources import category_03_pitch_arsenal as src03  # noqa: E402
from calculators.sources import category_04_plate_discipline as src04  # noqa: E402
from calculators.sources import category_05_ballpark_environment as src05  # noqa: E402
from calculators.sources import category_06_lineup_game_context as src06  # noqa: E402
from calculators.sources import category_08_batter_form as src08  # noqa: E402
from calculators.sources import category_09_pitcher_form as src09  # noqa: E402
from calculators.sources import category_10_defense_schedule as src10  # noqa: E402
from main import fetch_lineup, fetch_schedule, is_opener  # noqa: E402
from calculators.sources.category_07_bullpen_exposure import (  # noqa: E402
    fetch_pitching_game_logs,
)

# `ROADMAP.md`'s sample batter list, verbatim.
SAMPLE_BATTERS = (
    "Bryce Harper",
    "Yordan Alvarez",
    "Freddie Freeman",
    "Fernando Tatis Jr.",
    "Mike Trout",
    "Alec Burleson",
    "James Wood",
    "Ernie Clement",
    "Randy Arozarena",
    "Jake Mangum",
    "Matt Olson",
    "Ketel Marte",
    "Jacob Wilson",
)

# Calculators that return None in every code path by design, each with a named
# blocker. Counting these as coverage gaps would understate the model.
STRUCTURAL_NONE = {
    "CALC_46": "no betting-odds source (#48)",
    "CALC_66": "no Statcast OAA source (#46)",
    "CALC_67": "no Statcast OAA source (#46)",
    "CALC_68": "needs the league pull #38 blocks (#46)",
}

# Keys whose value is an indicator or a signed delta, where 0.0 is a fully
# resolved answer rather than a weak one. Reported separately so a zero here is
# not read as a near-miss.
INDICATOR_KEYS = {
    "CALC_45",
    "CALC_51",
    "CALC_69",
    "CALC_70",
    "CALC_70_TZ_SHIFT",
    "CALC_70_DAYS_REST",
    "CALC_71",
    "CALC_34",
    "CALC_56",
    "CALC_57",
    "CALC_62",
    "CALC_63",
    "CALC_63_ZONE_DELTA",
    "CALC_64",
    "CALC_69_TURNAROUND_HOURS",
}

# Statcast populates `arm_angle` only from 2024 and only on swings; see the
# Category 3 notes. Averaging what is there is still the right estimate.
ARM_ANGLE_MIN_SEASON = 2024


def resolve_arm_angle(pitcher_pitches: list[dict]) -> float | None:
    """Mean arm angle over the starter's tracked pitches, or None.

    Derived here rather than left unset. `compute_category_02` takes
    `starter_arm_angle` as a parameter and no fetcher produces it, so passing
    None would make `CALC_14` look unreachable when it is merely unfetched.
    """
    angles = [
        float(p["arm_angle"]) for p in pitcher_pitches if p.get("arm_angle") is not None
    ]
    return statistics.fmean(angles) if angles else None


def classify(key: str, value: Any, supplied: bool) -> str:
    """One of resolved, indicator, structural, empty, or missing."""
    if value is not None:
        return "indicator" if key in INDICATOR_KEYS else "resolved"
    if key in STRUCTURAL_NONE:
        return "structural"
    return "empty" if supplied else "missing"


def fetch_hydrated_schedule(day: str) -> dict[int, dict]:
    """Raw schedule games keyed by `gamePk`, hydrated with venue and team.

    `main.fetch_schedule` projects a schedule record down to ids and start time
    and does **not** hydrate, so `environment_from_schedule` reading it gets
    `venue_id`, `day_night` and `home_team` all None, which silently empties
    `CALC_31`-`CALC_33`, `CALC_35`, `CALC_37`-`CALC_40` and `CALC_70`. That is
    issue #43, and it is a wiring gap rather than a data gap: the fields exist
    and cost nothing extra.

    This harness fetches the hydrated record itself so the measurement reflects
    what the calculators can do, and the report names #43 separately as what
    stands between that and a live run.
    """
    import requests

    from mlb_api import MLB_API_BASE

    resp = requests.get(
        f"{MLB_API_BASE}/schedule",
        params={"sportId": 1, "date": day, "hydrate": "team,venue,probablePitcher"},
        timeout=30,
    )
    resp.raise_for_status()
    return {
        game["gamePk"]: game
        for date_entry in resp.json().get("dates", [])
        for game in date_entry.get("games", [])
    }


def posted_hitters(day: str) -> tuple[dict[str, dict], list[dict]]:
    """Every posted hitter today, keyed by name, plus the raw schedule records."""
    games = fetch_schedule(day)
    hydrated = fetch_hydrated_schedule(day)
    hitters: dict[str, dict] = {}
    for game in games:
        lineup = fetch_lineup(game["gamePk"])
        for side, opposing in (
            ("home", "away_pitcher_id"),
            ("away", "home_pitcher_id"),
        ):
            for player in lineup[side]:
                hitters[player["fullName"]] = dict(
                    player,
                    game=game,
                    raw_game=hydrated.get(game["gamePk"], {}),
                    lineup=lineup,
                    side=side,
                    is_home=(side == "home"),
                    opposing_pitcher_id=game.get(opposing),
                )
    return hitters, games


def evaluate(hitter: dict, season: int, today: datetime.date) -> dict[str, Any]:
    """Run every category for one hitter and return {key: (value, supplied)}."""
    batter_id = hitter["id"]
    pitcher_id = hitter["opposing_pitcher_id"]
    game = hitter["game"]
    results: dict[str, tuple[Any, bool]] = {}

    hands = src02.fetch_handedness(
        [batter_id, pitcher_id] if pitcher_id else [batter_id]
    )
    # `fetch_handedness` keys these "bats" and "throws". Reading them as
    # "bat_side"/"pitch_hand" silently emptied all of Category 2 on the first
    # dry run, which the classifier then reported as an empty sample rather than
    # as the harness defect it was. Exactly why the missing/empty split exists.
    bat_side = (hands.get(batter_id) or {}).get("bats")
    throws = (hands.get(pitcher_id) or {}).get("throws") if pitcher_id else None

    batter_pitches = src02.fetch_batter_statcast(batter_id, season)
    pitcher_pitches = (
        src02.fetch_pitcher_statcast(pitcher_id, season) if pitcher_id else []
    )

    # Category 1
    bvp = (
        src01.fetch_bvp_stats(batter_id, pitcher_id)
        if pitcher_id
        else src01.empty_bvp()
    )
    bvp_pitches = (
        src01.fetch_bvp_statcast(batter_id, pitcher_id, season) if pitcher_id else []
    )
    for key, value in compute_category_01(bvp, season, bvp_pitches).items():
        results[key] = (value, bool(pitcher_id))

    # Category 2
    hitter_splits = src02.fetch_stat_splits(batter_id, "hitting", season)
    pitcher_splits = (
        src02.fetch_stat_splits(pitcher_id, "pitching", season) if pitcher_id else {}
    )
    arsenal = src03.fetch_pitcher_arsenal(pitcher_id, season) if pitcher_id else []
    # Read off Category 2's projection, not Category 3's. Both come from the same
    # cached Statcast frame, but only Category 2 projects `arm_angle`, so
    # reading the arsenal records returns None for every pitcher and CALC_14
    # looks unreachable when it is merely unprojected.
    arm_angle = resolve_arm_angle(pitcher_pitches)
    cat2 = compute_category_02(
        hitter_splits=hitter_splits,
        pitcher_splits=pitcher_splits,
        hitter_pitches=batter_pitches,
        pitcher_pitches=pitcher_pitches,
        batter_side=bat_side,
        pitcher_throws=throws,
        starter_arm_angle=arm_angle,
        league_baseline=baselines.load_league_platoon_baseline(),
        today=today,
    )
    for key, value in cat2.items():
        results[key] = (value, True)

    # Category 3
    batter_arsenal = src03.fetch_batter_arsenal(batter_id, season)
    for key, value in compute_category_03(batter_arsenal, arsenal).items():
        results[key] = (value, bool(pitcher_id))

    # Category 4
    cat4 = compute_category_04(
        src04.fetch_batter_discipline(batter_id, season),
        src04.fetch_pitcher_discipline(pitcher_id, season) if pitcher_id else [],
    )
    for key, value in cat4.items():
        results[key] = (value, True)

    # Category 5
    environment = src05.environment_from_schedule(hitter["raw_game"], hitter["is_home"])
    # Merged under, not over: `fetch_game_environment` returns the full key set
    # with None for anything the boxscore does not carry, so a plain `.update`
    # overwrites the schedule half with nulls. Cost `batter_is_home` and
    # `day_night` on the first dry run, which emptied CALC_37 and CALC_38.
    for key, value in src05.fetch_game_environment(game["gamePk"]).items():
        if value is not None:
            environment[key] = value
    cat5 = compute_category_05(
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
    for key, value in cat5.items():
        results[key] = (value, True)

    # Category 6
    side = hitter["lineup"][hitter["side"]]
    rates = src06.fetch_lineup_rates([p["id"] for p in side], season)
    cat6 = compute_category_06(
        lineup_spot=hitter.get("lineup_spot"),
        lineup=src06.lineup_by_spot(side, rates),
        pitcher_pitches=src06.fetch_pitcher_tto(pitcher_id, season)
        if pitcher_id
        else [],
        batter_pitches=src06.fetch_batter_tto(batter_id, season),
        batter_is_home=hitter["is_home"],
        game_context=baselines.load_league_game_context(),
    )
    for key, value in cat6.items():
        # CALC_46 needs a betting line nobody publishes to this repo.
        results[key] = (value, key != "CALC_46")

    # Category 8 and 9
    batter_form = src08.fetch_batter_form(batter_id, season)
    for key, value in compute_category_08(batter_form, today).items():
        results[key] = (value, True)
    pitcher_form = src09.fetch_pitcher_form(pitcher_id, season) if pitcher_id else []
    for key, value in compute_category_09(pitcher_form, today).items():
        results[key] = (value, bool(pitcher_id))

    # Category 10
    cat10 = compute_category_10(
        game={
            "game_pk": game["gamePk"],
            "game_date": today.isoformat(),
            "game_number": game.get("gameNumber", 1),
            "day_night": "day" if game["start_dt"].hour < 22 else "night",
            "venue_id": environment.get("venue_id"),
            "start_time": game["start_dt"],
        },
        game_log=src10.fetch_team_game_log(hitter["team_id"], today),
        ballparks=baselines.load_ballparks(),
        bvp=bvp,
    )
    for key, value in cat10.items():
        results[key] = (value, key not in ("CALC_66", "CALC_67", "CALC_68"))

    # Category 11
    cat11 = compute_category_11(
        pitches=batter_form,
        bvp=results.get("CALC_01", (None, True))[0],
        platoon=results.get("CALC_09", (None, True))[0],
        pitch_type=results.get("CALC_20", (None, True))[0],
        lineup_spot_pa=results.get("CALC_41", (None, True))[0],
        ninth_inning_risk=results.get("CALC_45", (None, True))[0],
        today=today,
    )
    for key, value in cat11.items():
        results[key] = (value, True)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--out", default=None, help="write the full result as JSON")
    args = parser.parse_args()

    today = datetime.date.fromisoformat(args.date)
    season = today.year
    hitters, games = posted_hitters(args.date)
    print(f"{args.date}: {len(games)} games, {len(hitters)} posted hitters\n")

    opener_ids = {
        pid
        for pid, log in fetch_pitching_game_logs(
            {
                p
                for g in games
                for p in (g.get("home_pitcher_id"), g.get("away_pitcher_id"))
                if p
            },
            season,
        ).items()
        if is_opener(log)
    }

    report: dict[str, Any] = {"date": args.date, "players": {}, "absent": []}
    for name in SAMPLE_BATTERS:
        hitter = hitters.get(name)
        if hitter is None:
            report["absent"].append(name)
            print(f"  --   {name:22} not in a posted lineup today")
            continue
        try:
            results = evaluate(hitter, season, today)
        except Exception:  # noqa: BLE001 - one hitter must not abort the run
            print(f"  ERR  {name:22}\n{traceback.format_exc()}")
            report["players"][name] = {"error": traceback.format_exc()}
            continue

        buckets: dict[str, list[str]] = {}
        for key, (value, supplied) in results.items():
            buckets.setdefault(classify(key, value, supplied), []).append(key)
        report["players"][name] = {
            "player_id": hitter["id"],
            "opposing_pitcher_id": hitter["opposing_pitcher_id"],
            "faces_opener": hitter["opposing_pitcher_id"] in opener_ids,
            "total_keys": len(results),
            "buckets": {k: sorted(v) for k, v in buckets.items()},
        }
        usable = len(buckets.get("resolved", [])) + len(buckets.get("indicator", []))
        print(
            f"  ok   {name:22} {usable:3}/{len(results)} usable  "
            f"(empty {len(buckets.get('empty', [])):2}, "
            f"structural {len(buckets.get('structural', [])):2}, "
            f"missing {len(buckets.get('missing', [])):2})"
            + (
                "  [faces an opener]"
                if hitter["opposing_pitcher_id"] in opener_ids
                else ""
            )
        )

    if args.out:
        with open(args.out, "w") as handle:
            json.dump(report, handle, indent=1)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
