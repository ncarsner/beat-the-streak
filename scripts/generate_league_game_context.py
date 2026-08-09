"""Generate the league game-context constants `CALC_45` measures against.

Run on demand, never during a daily run or a test:

    uv run python -m scripts.generate_league_game_context

Writes `calculators/data/league_game_context.json`.

What it measures
----------------
How often the home team never bats in the bottom of the ninth, because it is
already ahead. `CALC_45` needs this to turn the ROADMAP's conditional "home
hitters lose 0.5 PA if the team is leading in the 9th" into the unconditional
expectation a projection actually needs, since at pick time nobody knows whether
today's home team will be leading.

Games are counted only when they reached nine innings and finished, so a
rain-shortened seven-inning game is excluded rather than scored as a skipped
ninth. A home half-inning that was never played carries no `runs` value in the
linescore, which is the signal used.

Why this one is not blocked by #38
----------------------------------
Every other league-reference constant in this repo rests on a convenience sample
because `pybaseball.statcast()` is broken at the pinned version. This one comes
from the MLB Stats API's own schedule, so it is a genuine league census: 1,760
games across the 2026 regular season through 8 August, not a handful of probe
players.

The response is large, and this repo has been burned by a silently truncated
one before (see `LEADERBOARD_LIMIT` in `generate_league_platoon_baseline.py`).
The ranged request's total was therefore cross-checked against a day-by-day sum
over the same window, 136 separate requests: both returned 1,760 games and 780
skipped ninths, so the range is not truncated.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import requests

from mlb_api import MLB_API_BASE

OUTPUT_PATH = pathlib.Path("calculators/data/league_game_context.json")

# A game must reach this many innings to be evidence about the ninth at all.
REGULATION_INNINGS = 9

# Plate appearances a home team gives up when its half of the ninth is not
# played, spread over the nine lineup spots. This is the ROADMAP's own figure for
# CALC_45 and it is not measured here: the linescore reports runs and hits per
# inning, not plate appearances. It is independently plausible, since a
# half-inning averages roughly 4.3 plate appearances and 4.3 / 9 is 0.48.
PA_LOST_PER_SKIPPED_NINTH = 0.5


def skipped_ninth_rate(games) -> dict:
    """Count how often the home half of the ninth was never played.

    Pure: *games* is an iterable of raw schedule game objects. Split out from the
    fetch so the counting rule is testable without a network call, the same shape
    as `aggregate_platoon_baseline`.
    """
    counted = skipped = 0
    for game in games:
        if (game.get("status") or {}).get("abstractGameState") != "Final":
            continue
        innings = (game.get("linescore") or {}).get("innings") or []
        if len(innings) < REGULATION_INNINGS:
            continue
        counted += 1
        home_ninth = innings[REGULATION_INNINGS - 1].get("home") or {}
        if home_ninth.get("runs") is None:
            skipped += 1

    return {
        "rate": skipped / counted if counted else None,
        "denominator": counted,
        "skipped": skipped,
    }


def fetch_season_games(season: int, through: date):
    """Yield every regular-season game of *season* up to *through*."""
    resp = requests.get(
        f"{MLB_API_BASE}/schedule",
        params={
            "sportId": 1,
            "startDate": f"{season}-01-01",
            "endDate": through.isoformat(),
            "gameType": "R",
            "hydrate": "linescore",
        },
        timeout=120,
    )
    resp.raise_for_status()
    for day in resp.json().get("dates", []):
        yield from day.get("games", [])


def main(season: int | None = None) -> dict:
    today = date.today()
    season = season or today.year
    ninth = skipped_ninth_rate(fetch_season_games(season, today))

    document = {
        "season": season,
        "generated": today.isoformat(),
        "source": f"{MLB_API_BASE}/schedule?gameType=R&hydrate=linescore",
        "note": (
            "skipped_ninth is the share of completed regular-season games "
            "reaching 9 innings in which the home team never batted in the "
            "bottom of the ninth. pa_lost_per_skipped_ninth is the ROADMAP's own "
            "figure for CALC_45, not measured here: the linescore reports runs "
            "and hits per inning, not plate appearances."
        ),
        "skipped_ninth": ninth,
        "pa_lost_per_skipped_ninth": PA_LOST_PER_SKIPPED_NINTH,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n")
    return document


if __name__ == "__main__":
    doc = main()
    ninth = doc["skipped_ninth"]
    print(f"wrote {OUTPUT_PATH} (season {doc['season']})")
    print(
        f"  skipped ninth: {ninth['skipped']}/{ninth['denominator']} "
        f"({ninth['rate'] * 100:.2f}%)"
    )
