"""Network fetchers that feed the Category 6 lineup and game-context calculators.

Two fetchers and one omission.

`fetch_lineup_rates` resolves the season on-base, slugging, and OPS lines for a
whole batting order in **one** request, which is what makes `CALC_42` and
`CALC_43` cheap: they each need a *different* hitter's line than the one being
evaluated, so a per-player fetch would cost nine requests to serve nine hitters
and every one of those lines would be fetched twice as its neighbours came up.

`fetch_pitcher_tto` and `fetch_batter_tto` are thin projections over Category 2's
`_fetch_season_statcast`, so a Category 6 pull shares its network call with
whatever Categories 1 through 5, 8, and 9 already pulled for the same player and
season.

There is deliberately **no fetcher for `CALC_46`**. It wants a betting market's
run total, which no source available to this project publishes; the calculator
takes the number as a parameter and returns None until something supplies it.

Why the two times-through-order field tuples differ
---------------------------------------------------
The pitcher side needs `inning` and `outs_when_up` to tell a start from a relief
outing, and `batter` to count repeat encounters. The batter side needs `inning`
to guard against a late substitution, and `pitcher` to identify the starter, but
**not** `outs_when_up`: it cannot use the start test at all. See
`calculators.category_06_lineup_game_context.batter_tto` for why, and for what
happens if the rule is reused anyway.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date

import requests

from calculators.sources.category_02_platoon_splits import (
    _fetch_season_statcast,
    _normalize_pitches,
)
from mlb_api import MLB_API_BASE

# /people takes ids in the query string; chunk so the URL stays a sane length.
# Matches the chunk size `generate_league_platoon_baseline` settled on.
PEOPLE_CHUNK = 300

# Season rate fields CALC_42 and CALC_43 read, plus the plate-appearance count
# that becomes each `Rate`'s denominator. The API serves the three rates as
# strings (".375"), and as ".---" for a hitter with no plate appearances, which
# is why the calculators parse defensively rather than trusting a float.
LINEUP_RATE_FIELDS = ("obp", "slg", "ops", "plateAppearances")

CATEGORY_06_PITCHER_FIELDS = (
    "game_date",
    "game_pk",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "events",
    "inning",
    "outs_when_up",
    "batter",
)

CATEGORY_06_BATTER_FIELDS = (
    "game_date",
    "game_pk",
    "game_type",
    "at_bat_number",
    "pitch_number",
    "events",
    "inning",
    "pitcher",
)


def empty_lineup_rates() -> dict:
    """What a failed or absent lineup-rate lookup normalizes to."""
    return {}


def _season_line(person: dict) -> dict:
    """Project one hydrated `/people` record to the rate fields, or {}."""
    for group in person.get("stats") or []:
        for split in group.get("splits") or []:
            stat = split.get("stat") or {}
            return {field: stat.get(field) for field in LINEUP_RATE_FIELDS}
    return {}


def fetch_lineup_rates(
    player_ids: Collection[int],
    season: int | None = None,
) -> dict[int, dict]:
    """Return season OBP/SLG/OPS lines for every id, batched into one request.

    Issues ``GET /people?personIds=<csv>&hydrate=stats(group=[hitting],
    type=[season],season=<year>)``. One request covers an entire batting order,
    the same batching `fetch_handedness` uses and for the same reason.

    An id absent from the response, or one with no hitting line, is absent from
    the returned mapping; callers use ``.get(id)``. A pitcher id resolves to an
    empty line rather than raising, which is the right shape for a caller that
    passes a whole roster without filtering first.

    Returns an empty mapping on request failure rather than raising, matching the
    error contract of every other fetcher in this package.
    """
    season = season or date.today().year
    ids = sorted({int(pid) for pid in player_ids})
    if not ids:
        return empty_lineup_rates()

    lines: dict[int, dict] = {}
    for start in range(0, len(ids), PEOPLE_CHUNK):
        chunk = ids[start : start + PEOPLE_CHUNK]
        try:
            resp = requests.get(
                f"{MLB_API_BASE}/people",
                params={
                    "personIds": ",".join(str(i) for i in chunk),
                    "hydrate": (
                        f"stats(group=[hitting],type=[season],season={season})"
                    ),
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"Lineup rate fetch error ({exc})")
            continue
        for person in resp.json().get("people", []):
            line = _season_line(person)
            if line:
                lines[person["id"]] = line
    return lines


def lineup_by_spot(
    side: Sequence[dict],
    rates: dict[int, dict],
) -> dict[int, dict]:
    """Rekey *rates* from player id to batting-order spot.

    *side* is one side of `main.fetch_lineup`'s return value: a **list** of
    ``{"id", "fullName", "team_id", "lineup_spot"}`` records in batting order.
    Taking the list rather than a prebuilt ``{spot: id}`` mapping is deliberate,
    because building that mapping is where the substitution bug lives (below) and
    every caller would have to get it right independently.

    Pure, no network. The rekeying is the only step between a batched fetch and
    what `CALC_42` and `CALC_43` want, and keeping it here means the calculators
    never see a player id at all.

    **The first player at a spot wins, and that is not arbitrary.** A boxscore's
    `battingOrder` encodes the spot in three digits, so ``"100"`` is the posted
    starter at spot 1 and ``"101"`` is the first substitute to bat there;
    `main.batting_order_spot` divides by 100, which maps both to spot 1. The
    array lists them in that order, so taking the first occurrence keeps the
    posted starter, which is who `CALC_42` and `CALC_43` are about: a projection
    made before first pitch cannot know about a replacement who has not happened
    yet. A plain dict comprehension would instead keep whichever came last.

    Duplicates do not arise from a *posted* lineup, which is what this tool
    reads: all six games sampled on 2026-08-07 carried exactly nine entries with
    nine distinct spots. They appear once a game is under way, which is when a
    backtest over completed games would hit them.
    """
    lineup: dict[int, dict] = {}
    for player in side or []:
        spot = player.get("lineup_spot")
        player_id = player.get("id")
        if spot is None or spot in lineup or player_id not in rates:
            continue
        lineup[spot] = rates[player_id]
    return lineup


def fetch_pitcher_tto(pitcher_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *pitcher_id* threw in *season*.

    Feeds the pitcher leg of `CALC_44`. Statcast begins in 2015, so an earlier
    season returns []. Returns [] rather than raising on failure.
    """
    return _project("pitcher", pitcher_id, season, CATEGORY_06_PITCHER_FIELDS)


def fetch_batter_tto(batter_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw in *season*.

    Feeds the batter leg of `CALC_44`. Statcast begins in 2015, so an earlier
    season returns []. Returns [] rather than raising on failure.
    """
    return _project("batter", batter_id, season, CATEGORY_06_BATTER_FIELDS)


def _project(role: str, player_id: int, season: int | None, fields) -> list[dict]:
    try:
        frame = _fetch_season_statcast(role, player_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast TTO fetch failed for {role} {player_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame, fields)
