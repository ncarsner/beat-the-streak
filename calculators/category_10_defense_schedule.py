"""Category 10: Defense, Umpires & Schedule Fatigue (`CALC_66`-`CALC_71`).

Pure functions; the network half lives in
`calculators.sources.category_10_defense_schedule`. See `ROADMAP.md` for the
source definition of each `CALC_NN`.

**Three of the six have no reachable data source, and they ship as signatures.**
`CALC_66` and `CALC_67` need Statcast Outs Above Average, which pybaseball 2.0.0
does not expose (checked directly: its `fielding` helpers are Lahman and FanGraphs
data, not Statcast OAA). `CALC_68` needs an umpire's strike-zone size index, which
requires aggregating league-wide pitch data by umpire, and Statcast publishes no
umpire column at all -- the join would have to go through `game_pk` on a
league-wide pull that issue #38 blocks. All three take their value as a parameter
and return None until something supplies it, exactly as `CALC_46` does. Issue #46
tracks the decision.

The umpire's **identity** is reachable today and is not the blocked half; see
`calculators.sources.category_10_defense_schedule.fetch_home_plate_umpire`.

**The three that do work are schedule arithmetic**, and they share one input: the
team's own recent game log. `CALC_69` and `CALC_70` both ask "what happened
between the previous game and this one", so they take the same pair of records.

**Nothing here is wired into a run.** The calculators are built and tested in
isolation and get consumed compositely in a later release.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from calculators.common import DELTA, MULTIPLIER, Rate

# Mean radius of the Earth in statute miles, for the great-circle distance
# `CALC_70` reports. Validated against a real leg: Wrigley Field to Yankee
# Stadium solves to 715.1 miles, against roughly 713 by independent reckoning
# (2026-08-09). A swapped latitude and longitude, or a degrees-for-radians slip,
# yields a plausible-looking wrong number, which is why this was checked against
# a known pair before anything depended on it.
EARTH_RADIUS_MILES = 3958.7613

# Values the MLB Stats API uses in a schedule record's `dayNight` field.
DAY = "day"
NIGHT = "night"


# ---------------------------------------------------------------------------
# The team game log
# ---------------------------------------------------------------------------


def _game_sort_key(game: dict[str, Any]) -> tuple[str, int]:
    """Order games by date, then by game number within a doubleheader.

    **Not by date alone.** Category 8's `game_log` is keyed on `game_pk` rather
    than `game_date` for the same reason: a date string collapses the two halves
    of a doubleheader into one game. Here it would do worse than collapse them,
    because `CALC_69` and `CALC_70` both ask what the *previous* game was, and on
    a doubleheader date that question has two different answers. Game two's
    previous game is game one: zero travel miles and a turnaround measured in
    hours, which is precisely the fatigue these calculators exist to detect.
    """
    return (game.get("game_date") or "", int(game.get("game_number") or 1))


def previous_game(
    game_log: Sequence[dict[str, Any]],
    game_pk: Any,
) -> dict[str, Any] | None:
    """The game immediately before *game_pk* in *game_log*, or None.

    Returns None when *game_pk* is the first game on record or is absent, which
    is the honest answer: a season opener has no previous game, so there is no
    turnaround and no travel leg to measure.
    """
    ordered = sorted(game_log or [], key=_game_sort_key)
    for index, game in enumerate(ordered):
        if game.get("game_pk") == game_pk:
            return ordered[index - 1] if index else None
    return None


# ---------------------------------------------------------------------------
# CALC_66 / CALC_67 -- defensive Outs Above Average
# ---------------------------------------------------------------------------


def _oaa(value: float | None, innings: int | None) -> DELTA | None:
    if value is None:
        return None
    return DELTA(Rate(float(value), int(innings) if innings else 1))


def calc_66_infield_defense(
    infield_oaa: float | None = None,
    innings: int | None = None,
) -> DELTA | None:
    """CALC_66: the opposing infield's Outs Above Average.

    Positive means the infield converts more ground balls into outs than an
    average one, which *lowers* a hitter's chance of a hit. The sign is therefore
    opposite in effect to most of the model's inputs, and `DELTA` because outs
    above average is a count, not a rate.

    **Returns None in every current code path.** There is no reachable source:
    pybaseball 2.0.0 exposes no fielding-leaderboard function, and the MLB Stats
    API publishes no OAA. Issue #46 tracks the decision about whether to read
    Savant's fielding leaderboard the way `scripts/generate_park_factors.py`
    reads the park-factor one. The signature exists so the shape is recorded and
    a table plugs straight in.

    *innings* is the fielding sample behind the figure when a source provides it,
    and 1 when it does not, in which case the denominator is not evidence weight.
    """
    return _oaa(infield_oaa, innings)


def calc_67_outfield_defense(
    outfield_oaa: float | None = None,
    innings: int | None = None,
) -> DELTA | None:
    """CALC_67: the opposing outfield's Outs Above Average.

    Same shape, same blocker, same issue as `CALC_66`. Split from it rather than
    parameterized because the ROADMAP names them separately and because they act
    on different batted-ball types: the infield figure bears on ground balls and
    the outfield one on fly balls and line drives, which means a consumer holding
    `CALC_33`'s spray distribution can weight them differently.
    """
    return _oaa(outfield_oaa, innings)


# ---------------------------------------------------------------------------
# CALC_68 -- umpire strike zone
# ---------------------------------------------------------------------------


def calc_68_umpire_zone(
    zone_index: float | None = None,
    league_mean_index: float | None = None,
) -> MULTIPLIER | None:
    """CALC_68: the home-plate umpire's zone size against the league mean.

    Above 1.0 means a wider zone than average, which favours the pitcher and
    lowers a hitter's chance of a hit.

    **Returns None in every current code path**, and needs *two* numbers that are
    not available rather than one. Statcast publishes no umpire column at all, so
    a per-umpire zone index has to be built by joining pitch data to officials
    through `game_pk` across a league-wide sample, which is what issue #38 blocks.
    The league mean of that index is unavailable for the same reason: it is a mean
    of a quantity that cannot be computed yet.

    The umpire's *identity* is not the blocked half. It arrives free on the
    boxscore request `main.fetch_lineup` already makes, and
    `fetch_home_plate_umpire` returns it today. Recorded so a future session does
    not re-derive that half.

    No default league mean is supplied. An invented reference would make every
    umpire look like a measured deviation from a real average, which is the
    constant-from-nowhere `CALC_34` refused to invent.
    """
    if zone_index is None or not league_mean_index:
        return None
    return MULTIPLIER(Rate(float(zone_index) / float(league_mean_index), 1))


# ---------------------------------------------------------------------------
# CALC_69 -- day after night
# ---------------------------------------------------------------------------


def calc_69_day_after_night(
    game: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """CALC_69: whether today is a day game following last night's night game.

    Two keys. `CALC_69` is the ROADMAP's condition as a 0/1 indicator: the
    previous game was at night, today's is in daylight, and they are on
    consecutive calendar days. `CALC_69_TURNAROUND_HOURS` is the hours between
    the two **first pitches**.

    **That second key is start-to-start, not rest, and the name says so
    deliberately.** A night game ending near 10:30pm before a 1:05pm start is
    about 14.5 hours between first pitches but nearer 11 hours of actual
    turnaround. Statcast and the schedule both publish start times and neither
    publishes the moment a game ended, so the honest quantity is the one that can
    be measured, under a name that cannot be mistaken for recovery time.

    Both are `DELTA`. The indicator is a condition rather than a magnitude: how
    much a day-after-night game costs a hitter is a league measurement issue #38
    blocks, so this reports whether the condition holds and issue #39 owns what
    it is worth. Neither denominator is a sample size.

    A hitter with no previous game on record returns None for both, which is
    correct for a season opener.
    """
    empty = {"CALC_69": None, "CALC_69_TURNAROUND_HOURS": None}
    if not game or not previous:
        return empty

    today = _parse_date(game.get("game_date"))
    yesterday = _parse_date(previous.get("game_date"))
    if today is None or yesterday is None:
        return empty

    consecutive = (today - yesterday).days == 1
    condition = (
        consecutive
        and previous.get("day_night") == NIGHT
        and game.get("day_night") == DAY
    )

    hours = None
    start, previous_start = game.get("start_time"), previous.get("start_time")
    if start is not None and previous_start is not None:
        hours = (start - previous_start).total_seconds() / 3600.0

    return {
        "CALC_69": DELTA(Rate(1.0 if condition else 0.0, 1)),
        "CALC_69_TURNAROUND_HOURS": (
            DELTA(Rate(hours, 1)) if hours is not None else None
        ),
    }


def _parse_date(value: Any) -> datetime.date | None:
    if isinstance(value, datetime.date):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# CALC_70 -- travel and timezone displacement
# ---------------------------------------------------------------------------


def great_circle_miles(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
) -> float | None:
    """Great-circle distance in statute miles between two ballpark records.

    Each argument is a `ballparks.json` venue entry. Returns None when either
    lacks coordinates rather than treating a missing venue as zero travel, which
    would read as a home stand.
    """
    if not first or not second:
        return None
    try:
        lat1, lon1 = float(first["latitude"]), float(first["longitude"])
        lat2, lon2 = float(second["latitude"]), float(second["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def utc_offset_hours(
    venue: dict[str, Any] | None, on: datetime.date | None
) -> float | None:
    """A venue's UTC offset in hours on a given date, resolved from its IANA zone.

    **Not read from the table's `utc_offset_hours` field**, which is a snapshot
    taken when the table was generated and is wrong for most of the season. The
    stored value for Oracle Park is -7, its summer offset; in January it is -8.
    Arizona is the case that makes this matter rather than merely annoy: Chase
    Field does not observe daylight saving, so it sits at -7 year round, which
    means a July San Francisco to Phoenix trip crosses **no** time zones while an
    April one crosses one. Only resolving the zone at the game's own date gets
    that right, and `zoneinfo` is in the standard library, so it costs nothing.

    The stored offset is kept in the table as provenance, not as an input.
    """
    zone_id = (venue or {}).get("timezone_id")
    if not zone_id or on is None:
        return None
    try:
        zone = ZoneInfo(zone_id)
    except (KeyError, ValueError, OSError):
        return None
    offset = datetime.datetime(on.year, on.month, on.day, 12, tzinfo=zone).utcoffset()
    return offset.total_seconds() / 3600.0 if offset is not None else None


def calc_70_travel_displacement(
    game: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    ballparks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """CALC_70: the travel leg between the previous game and this one.

    Three keys, because the ROADMAP's "cross-country travel **without an off
    day**" is a rule over three separate measurements and this module reports the
    measurements rather than guessing the rule.

    - `CALC_70` is the great-circle distance in miles between the two venues.
      Zero for a game at the same park, which is a real reading.
    - `CALC_70_TZ_SHIFT` is the signed change in UTC offset in hours, positive
      travelling east. Resolved from each venue's IANA zone at its own game date,
      never from a stored offset; see `utc_offset_hours`.
    - `CALC_70_DAYS_REST` is calendar days between the two games, so 1 means a
      back-to-back and 2 means an off day intervened. Two games on one date, a
      doubleheader, give 0.

    All three are `DELTA` and none of their denominators is a sample size. Issue
    #39 owns how they combine into a penalty, which is why no threshold for
    "cross-country" appears here: that number would be a league constant this
    repo cannot currently measure.
    """
    empty = {
        "CALC_70": None,
        "CALC_70_TZ_SHIFT": None,
        "CALC_70_DAYS_REST": None,
    }
    if not game or not previous:
        return empty

    ballparks = ballparks or {}
    today = _parse_date(game.get("game_date"))
    before = _parse_date(previous.get("game_date"))

    here = ballparks.get(str(game.get("venue_id")))
    there = ballparks.get(str(previous.get("venue_id")))
    miles = great_circle_miles(there, here)

    shift = None
    offset_here = utc_offset_hours(here, today)
    offset_there = utc_offset_hours(there, before)
    if offset_here is not None and offset_there is not None:
        shift = offset_here - offset_there

    rest = (today - before).days if today and before else None

    return {
        "CALC_70": DELTA(Rate(miles, 1)) if miles is not None else None,
        "CALC_70_TZ_SHIFT": DELTA(Rate(shift, 1)) if shift is not None else None,
        "CALC_70_DAYS_REST": DELTA(Rate(float(rest), 1)) if rest is not None else None,
    }


# ---------------------------------------------------------------------------
# CALC_71 -- unfamiliarity
# ---------------------------------------------------------------------------


def calc_71_unfamiliarity(bvp: dict[str, Any] | None) -> DELTA | None:
    """CALC_71: whether this hitter has never faced this pitcher before.

    1.0 when the pair has no career plate appearances against each other, 0.0
    when they do. The ROADMAP frames it as a pitcher advantage on the first time
    through the order, which pairs it with `CALC_44`.

    **This is the one calculator in the model whose value *is* the empty
    sample**, and that inverts the convention everywhere else. Every other
    calculator reads a null career line as "no evidence" and returns None. Here a
    null career line is the finding.

    That makes it uniquely sensitive to a dropped request, because
    `calculators.sources.category_01_bvp_matchups.empty_bvp` produces exactly the
    same null career line on a network failure. It is also not hypothetical: the
    2026-08-07 probe found the same batter-pitcher pair returning a populated
    response one minute and an empty split list the next, which is why
    `parse_bvp_stats` falls back to the API total at all.

    So this reads `resolved`, which is True only when the API returned a
    recognized stat group. An unresolved payload returns None -- no evidence,
    which is the truth -- rather than reporting a confident first meeting off a
    failed fetch.
    """
    if not bvp or not bvp.get("resolved"):
        return None
    career = bvp.get("career") or {}
    plate_appearances = career.get("plateAppearances") or 0
    return DELTA(Rate(0.0 if plate_appearances else 1.0, int(plate_appearances)))


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_10(
    game: dict[str, Any] | None = None,
    game_log: Sequence[dict[str, Any]] | None = None,
    ballparks: dict[str, Any] | None = None,
    bvp: dict[str, Any] | None = None,
    infield_oaa: float | None = None,
    outfield_oaa: float | None = None,
    umpire_zone_index: float | None = None,
    league_mean_zone_index: float | None = None,
) -> dict[str, Any]:
    """Run every Category 10 calculator.

    Absent input resolves every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_09`.

    *game* is today's schedule record and *game_log* the team's recent games,
    both from `calculators.sources.category_10_defense_schedule`. The previous
    game is resolved from the log rather than passed separately, so a caller
    cannot accidentally supply a previous game from a different club.
    """
    previous = previous_game(game_log or [], (game or {}).get("game_pk"))

    results: dict[str, Any] = {
        "CALC_66": calc_66_infield_defense(infield_oaa),
        "CALC_67": calc_67_outfield_defense(outfield_oaa),
        "CALC_68": calc_68_umpire_zone(umpire_zone_index, league_mean_zone_index),
        "CALC_71": calc_71_unfamiliarity(bvp),
    }
    results.update(calc_69_day_after_night(game, previous))
    results.update(calc_70_travel_displacement(game, previous, ballparks))
    return results
