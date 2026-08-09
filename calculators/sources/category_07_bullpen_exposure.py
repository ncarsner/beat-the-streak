"""Network fetchers that feed the Category 7 opposing-bullpen calculators.

Three fetchers, two of which cover the whole category between them.

`fetch_active_pitchers` resolves who is on a club's active roster on a given
date, with each pitcher's throwing hand. `fetch_pitching_game_logs` resolves
every one of those pitchers' per-game lines for the season in a single batched
request. `fetch_bullpen` composes the two, so an entire opposing bullpen costs
**two requests** regardless of its size.

`fetch_reliever_pitches` is the odd one out and is optional; see below.

Why this category is not blocked
--------------------------------
It was carried as blocked for several sessions on the belief that bullpen
membership needs a roster feed this project does not have, and that the
starter/reliever split would have to be reconstructed from Statcast by
inverting Category 9's start test. Both were wrong, established by probe on
2026-08-09 and recorded on issue #47.

`GET /teams/{id}/roster?rosterType=active&date=` exists and honours `date`.
Checked at two in-season dates rather than one, because a silently ignored
`date` would return today's roster always, which would make every calculator
here unbacktestable against a past slate and so useless to #35. Team 147
returned 13 pitchers on 2026-04-15 and 13 on 2026-08-08, sharing 10, with the
three arrivals and three departures matching real roster movement.

The role split does not need Statcast either. It is carried in the game log:
every per-game line has `gamesStarted`, so a pitcher's relief work is exactly
his lines with `gamesStarted == 0`. That is better than a per-pitcher label,
because it lets `CALC_47` and `CALC_50` aggregate over relief *appearances*
rather than over pitchers, and a swingman then contributes his relief innings
without contributing his starts. See the calculator module for why that matters.

What is not reachable
---------------------
`CALC_50` asks for in-play rate **and** whiff rate. In-play comes free from the
counting line. Whiff does not: `stats=pitchArsenal` publishes only `percentage`,
`count`, `totalPitches`, `averageSpeed` and `type`, and `stats=expectedStatistics`
publishes only `avg`, `slg`, `woba` and `wobaCon`. Neither carries swings or
misses, so a real whiff rate needs pitch-level data.

`fetch_reliever_pitches` provides it, and is deliberately not called by
`fetch_bullpen`. It is one Statcast pull **per reliever**, so a seven-arm
bullpen costs seven network calls against the two the rest of the category
needs, and it buys one key. `CALC_50` therefore takes pitch records as an
optional argument on the `CALC_05`-`CALC_08` precedent: the in-play rate always
resolves and the whiff rate is opportunistic.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from typing import Any

import requests

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
)
from mlb_api import MLB_API_BASE

# `/people` takes ids in the query string; chunk so the URL stays a sane length.
# Matches what `fetch_lineup_rates` and `generate_league_platoon_baseline` use.
PEOPLE_CHUNK = 300

# The `position.abbreviation` identifying a pitcher on a roster entry, and the
# `status.code` for an active player. Every entry `rosterType=active` returned
# in the probe already carried status "A", but an entry is filtered on it anyway:
# a pitcher on the injured list is not in tonight's bullpen, and relying on the
# roster type alone to guarantee that is an assumption with no cost to remove.
PITCHER_POSITION = "P"
ACTIVE_STATUS = "A"

# Per-game fields the calculators read, mapped from the API's names to the
# module's. `gamesStarted` is 0 or 1 on a game line and becomes the boolean that
# separates a relief appearance from a start.
GAME_LOG_FIELDS = {
    "batters_faced": "battersFaced",
    "at_bats": "atBats",
    "hits": "hits",
    "walks": "baseOnBalls",
    "hit_by_pitch": "hitByPitch",
    "strike_outs": "strikeOuts",
    "pitches": "numberOfPitches",
    "outs": "outs",
    "holds": "holds",
    "saves": "saves",
}

CATEGORY_07_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "description",
    "events",
)


def empty_bullpen() -> list[dict]:
    """What a failed or absent bullpen lookup normalizes to."""
    return []


def _pitch_hand(person: dict) -> str | None:
    return ((person.get("pitchHand") or {}).get("code")) or None


def fetch_active_pitchers(
    team_id: int,
    on: date | None = None,
) -> list[dict]:
    """Return *team_id*'s active pitchers on *on*, with throwing hands.

    Issues one ``GET /teams/{id}/roster?rosterType=active&date=...`` hydrated
    with `person(pitchHand)`. Each record is ``{"id", "full_name", "pitch_hand"}``.

    *on* defaults to today. Passing an explicit date is what lets a backtest
    reconstruct the bullpen a hitter actually faced rather than the one that
    exists now.

    Returns [] on request failure rather than raising, matching every other
    fetcher's error contract.
    """
    on = on or date.today()
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/teams/{team_id}/roster",
            params={
                "rosterType": "active",
                "date": on.isoformat(),
                "hydrate": "person(pitchHand)",
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Active roster fetch error for team {team_id} ({exc})")
        return []

    pitchers = []
    for entry in resp.json().get("roster") or []:
        position = (entry.get("position") or {}).get("abbreviation")
        status = (entry.get("status") or {}).get("code")
        person = entry.get("person") or {}
        if position != PITCHER_POSITION or status != ACTIVE_STATUS:
            continue
        if person.get("id") is None:
            continue
        pitchers.append(
            {
                "id": person["id"],
                "full_name": person.get("fullName"),
                "pitch_hand": _pitch_hand(person),
            }
        )
    return pitchers


def _game_line(split: dict) -> dict:
    """Project one game-log split into what the calculators read."""
    stat = split.get("stat") or {}
    line = {
        "game_date": split.get("date"),
        "game_type": split.get("gameType"),
        "game_pk": (split.get("game") or {}).get("gamePk"),
        "started": bool(stat.get("gamesStarted")),
    }
    for name, field in GAME_LOG_FIELDS.items():
        line[name] = stat.get(field) or 0
    return line


def fetch_pitching_game_logs(
    player_ids: Collection[int],
    season: int | None = None,
) -> dict[int, list[dict]]:
    """Return per-game pitching lines for every id, batched into one request.

    Issues ``GET /people?personIds=<csv>&hydrate=stats(group=[pitching],
    type=[gameLog],season=<year>)``. One request covers a whole bullpen, the
    same batching `fetch_lineup_rates` and `fetch_handedness` use.

    Each line carries `game_date`, `game_type`, `game_pk`, `started`, and the
    counting fields in `GAME_LOG_FIELDS`. `game_type` is carried so the
    calculators can drop non-competitive games through
    `calculators.common.is_competitive`, which is the filter issue #42 is open
    about Categories 1 through 4 missing.

    An id with no pitching log is absent from the mapping; callers use `.get`.
    Returns an empty mapping on request failure rather than raising.
    """
    season = season or date.today().year
    ids = sorted({int(pid) for pid in player_ids})
    if not ids:
        return {}

    logs: dict[int, list[dict]] = {}
    for start in range(0, len(ids), PEOPLE_CHUNK):
        chunk = ids[start : start + PEOPLE_CHUNK]
        try:
            resp = requests.get(
                f"{MLB_API_BASE}/people",
                params={
                    "personIds": ",".join(str(i) for i in chunk),
                    "hydrate": (
                        f"stats(group=[pitching],type=[gameLog],season={season})"
                    ),
                },
                timeout=60,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"Pitching game log fetch error ({exc})")
            continue
        for person in resp.json().get("people") or []:
            lines: list[dict] = []
            for group in person.get("stats") or []:
                for split in group.get("splits") or []:
                    lines.append(_game_line(split))
            if lines:
                logs[person["id"]] = lines
    return logs


def fetch_bullpen(
    team_id: int,
    on: date | None = None,
    season: int | None = None,
) -> list[dict]:
    """Return *team_id*'s active pitchers with their season game logs attached.

    Two requests total, whatever the roster size: one roster, one batched game
    log. Each record is ``{"id", "full_name", "pitch_hand", "games": [...]}``,
    which is the input shape every Category 7 calculator takes.

    **Every active pitcher is returned, starters included.** Deciding who counts
    as bullpen is a judgement over the game log rather than a property of the
    roster, so it belongs in the calculator module where it is pure and
    testable; see `bullpen_pool`.

    *on* dates the roster and *season* the logs. They are separate arguments
    because a backtest against an early-April date still wants that season's
    full log for the season-scoped rates.
    """
    on = on or date.today()
    pitchers = fetch_active_pitchers(team_id, on)
    if not pitchers:
        return empty_bullpen()

    logs = fetch_pitching_game_logs([p["id"] for p in pitchers], season or on.year)
    return [{**pitcher, "games": logs.get(pitcher["id"], [])} for pitcher in pitchers]


def fetch_reliever_pitches(
    pitcher_id: int,
    season: int | None = None,
) -> list[dict]:
    """Return one normalized record per pitch *pitcher_id* threw in *season*.

    Feeds `CALC_50`'s whiff key, the one thing in this category the Stats API
    does not publish. **One network call per reliever**, which is why nothing
    calls this automatically: see the module docstring for the cost argument.

    Statcast begins in 2015, so an earlier season returns []. Returns [] rather
    than raising on failure.
    """
    try:
        frame = _fetch_season_statcast("pitcher", pitcher_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast bullpen fetch failed for pitcher {pitcher_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, CATEGORY_07_PITCH_FIELDS)


def attach_category_07(
    team_id: int,
    on: date | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    """Fetch everything `compute_category_07`'s bullpen arguments need.

    Convenience wrapper mirroring `attach_category_01`. Does **not** fetch
    pitch-level data; pass `reliever_pitches` yourself if the whiff key is
    wanted, having read the cost note in the module docstring.
    """
    return {"bullpen": fetch_bullpen(team_id, on, season)}
