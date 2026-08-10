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
import sys
import traceback
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from calculators.pipeline import assemble  # noqa: E402
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
    """Run every category for one hitter and return {key: (value, supplied)}.

    A thin wrapper over `calculators.pipeline.assemble`, which is what `main.py`
    uses for the `Model` column. Sharing it is the point: a coverage measurement
    taken against a different assembly than the one that ships would measure the
    wrong thing.

    The `supplied` flag is this script's own addition, and it is what separates
    "the input arrived and was empty" from "this harness did not pass it". Only
    the keys with no reachable source at all are marked unsupplied.
    """
    results = assemble(
        hitter["id"],
        hitter["opposing_pitcher_id"],
        raw_game=hitter["raw_game"],
        game_pk=hitter["game"]["gamePk"],
        team_id=hitter.get("team_id"),
        lineup_side=hitter["lineup"][hitter["side"]],
        lineup_spot=hitter.get("lineup_spot"),
        is_home=hitter["is_home"],
        game_number=hitter["game"].get("gameNumber", 1),
        start_time=hitter["game"]["start_dt"],
        season=season,
        today=today,
    )
    return {key: (value, key not in STRUCTURAL_NONE) for key, value in results.items()}


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
