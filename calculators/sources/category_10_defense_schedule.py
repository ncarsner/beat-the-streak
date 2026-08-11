"""Network fetchers that feed the Category 10 defense and schedule calculators.

Two fetchers, and three deliberate omissions.

`fetch_team_game_log` pulls a club's recent schedule, which is the single input
`CALC_69` and `CALC_70` share: both ask what happened between the previous game
and today's, so both need the same ordered list of games with their start times,
day/night flag, and venue.

`fetch_home_plate_umpire` returns the official working the plate. It exists even
though `CALC_68` cannot be computed, because the identity is the reachable half
and re-deriving that later would waste a session. See the calculator's docstring
for which half is blocked and by what.

**There is no fetcher for `CALC_66`, `CALC_67`, or `CALC_68`'s zone index.**
Those need Outs Above Average and a per-umpire zone measurement, neither of which
is reachable; the calculators take their values as parameters. Issue #46 tracks
the decision.

The umpire shares the weather's publish clock
---------------------------------------------
`officials` comes off the same boxscore as `Weather` and `Wind`, and behaves the
same way: reliably present after a game, sporadically present before one. The
2026-08-09 sample had 2 of 8 Preview-state games carrying a populated officials
array. Anything wiring this in needs the cache-hit re-resolution described in the
Category 5 source module and tracked in the same issue.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import requests

from mlb_api import MLB_API_BASE

# How far back a game log reaches by default. `CALC_69` and `CALC_70` only ever
# look at the immediately preceding game, so this needs to cover the longest
# plausible gap between games rather than a whole season: an All-Star break plus
# a stretch on the injured list would exceed it, and in that case the previous
# game is genuinely too long ago to be a fatigue signal.
DEFAULT_LOOKBACK_DAYS = 14

# The `officialType` value identifying the plate umpire, whose zone is the one
# that matters. The array also carries the three base umpires.
HOME_PLATE = "Home Plate"


def _parse_start(value: Any) -> datetime | None:
    """Parse a schedule record's ISO-8601 `gameDate` into an aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _game_record(game: dict) -> dict:
    """Project one raw schedule game into what the calculators read.

    `game_number` is carried because `CALC_69` and `CALC_70` resolve the
    *previous* game, and on a doubleheader date the date alone does not order
    the two halves.
    """
    return {
        "game_pk": game.get("gamePk"),
        "game_date": game.get("officialDate"),
        "game_number": game.get("gameNumber") or 1,
        "day_night": game.get("dayNight"),
        "venue_id": (game.get("venue") or {}).get("id"),
        "start_time": _parse_start(game.get("gameDate")),
    }


def fetch_team_game_log(
    team_id: int,
    through: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict]:
    """Return *team_id*'s regular-season games in the window ending at *through*.

    One `GET /schedule?teamId=...` request covering the whole window, ordered
    oldest first. Records carry `game_pk`, `game_date`, `game_number`,
    `day_night`, `venue_id`, and `start_time`.

    *team_id* should be the club whose schedule is wanted. Note that
    `main.fetch_lineup` supplies `parentTeamId` per player, which for a recent
    callup is the major-league parent rather than wherever he has been playing;
    that is the right club here, since these calculators are about today's game.

    Returns [] on request failure rather than raising, matching every other
    fetcher's error contract.
    """
    through = through or date.today()
    start = through - timedelta(days=lookback_days)
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={
                "sportId": 1,
                "teamId": team_id,
                "startDate": start.isoformat(),
                "endDate": through.isoformat(),
                "gameType": "R",
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Team game log fetch error for team {team_id} ({exc})")
        return []

    games = []
    for day in resp.json().get("dates", []):
        for game in day.get("games", []):
            games.append(_game_record(game))
    return sorted(games, key=lambda g: (g["game_date"] or "", g["game_number"]))


def fetch_home_plate_umpire(game_pk: int) -> dict | None:
    """Return ``{"id", "fullName"}`` for the plate umpire, or None.

    Issues one `GET /game/{game_pk}/boxscore`, the same request
    `main.fetch_lineup` makes. Returns None when the assignment has not published
    yet, which before a game is the common case rather than an error.

    This is the half of `CALC_68` that works. The zone-size index it would be
    joined to does not exist yet; see the calculator.
    """
    try:
        resp = requests.get(f"{MLB_API_BASE}/game/{game_pk}/boxscore", timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Umpire fetch error for game {game_pk} ({exc})")
        return None

    for official in resp.json().get("officials") or []:
        if official.get("officialType") == HOME_PLATE:
            person = official.get("official") or {}
            if person.get("id") is not None:
                return {"id": person["id"], "fullName": person.get("fullName")}
    return None
