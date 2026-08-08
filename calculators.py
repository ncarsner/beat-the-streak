"""Category 1 probability calculators — direct pitcher-vs-batter (BvP) matchups.

Pure functions over an already-normalized BvP payload; no network I/O lives here.
See `ROADMAP.md` for the source definitions of each `CALC_nn`.

The normalized payload is produced by `parse_bvp_stats` and has the shape::

    {"career": <line> | None, "by_season": {2026: <line>, ...}}

where a *line* is a dict of counting stats (`plateAppearances`, `atBats`, `hits`,
`strikeOuts`, `baseOnBalls`). Every calculator returns a `BvPRate` or ``None`` when
the matchup has no plate appearances to divide by — an empty history is absence of
evidence, not a 0.0 rate, and callers must be able to tell the two apart.
"""

from typing import Any, Iterable, NamedTuple


# Counting stats carried through from a MLB Stats API vsPlayer split.
COUNTING_STATS = ("plateAppearances", "atBats", "hits", "strikeOuts", "baseOnBalls")

# CALC_03's window: the current season plus the two prior calendar years.
RECENT_WINDOW_YEARS = 3


class BvPRate(NamedTuple):
    """A rate paired with the plate appearances it was computed over.

    `denominator` is the sample size, kept alongside the rate so downstream
    weighting (CALC_75) can shrink a 1-for-2 career line toward a prior instead
    of reading it as .500.
    """

    rate: float
    denominator: int


def empty_line() -> dict[str, int]:
    """Return a zeroed counting-stat line."""
    return dict.fromkeys(COUNTING_STATS, 0)


def aggregate_lines(lines: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Sum *lines* field by field, treating missing keys as 0."""
    total = empty_line()
    for line in lines:
        for stat in COUNTING_STATS:
            total[stat] += line.get(stat, 0) or 0
    return total


def parse_bvp_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw `stats=vsPlayer` response into the calculator payload.

    A single `vsPlayer` request returns one split per season the pair has faced
    each other, plus a `vsPlayerTotal` group holding the API's own career line
    (that group is appended unasked, and is sometimes repeated).

    Career is summed from the per-season splits rather than read from
    `vsPlayerTotal`, which has been observed to disagree with them — a batter
    whose only season split showed 3 PA carried a 2 PA career total, which would
    make CALC_01's sample smaller than CALC_03's window over the same matchup.
    The API total is used only when no season splits came back at all. Malformed
    or empty responses normalize to ``{"career": None, "by_season": {}}`` rather
    than raising.
    """
    api_total: dict[str, int] | None = None
    by_season: dict[int, dict[str, int]] = {}

    for group in payload.get("stats") or []:
        group_name = (group.get("type") or {}).get("displayName")
        for split in group.get("splits") or []:
            line = {
                stat: split.get("stat", {}).get(stat, 0) or 0 for stat in COUNTING_STATS
            }
            if group_name == "vsPlayer":
                season = split.get("season")
                if season is None:
                    continue
                year = int(season)
                # A season can arrive as more than one split (a midseason trade
                # splits it by team); sum them rather than letting one win.
                by_season[year] = aggregate_lines([by_season.get(year, {}), line])
            elif group_name == "vsPlayerTotal":
                api_total = line

    career = aggregate_lines(by_season.values()) if by_season else api_total

    return {"career": career, "by_season": by_season}


def _rate(numerator: int, denominator: int) -> BvPRate | None:
    if denominator <= 0:
        return None
    return BvPRate(numerator / denominator, denominator)


def _season_window(bvp: dict[str, Any], season: int, years: int) -> dict[str, int]:
    """Aggregate the seasons in the inclusive window ending at *season*."""
    earliest = season - years + 1
    return aggregate_lines(
        line
        for year, line in bvp.get("by_season", {}).items()
        if earliest <= year <= season
    )


def calc_01_bvp_career_hit_rate(bvp: dict[str, Any]) -> BvPRate | None:
    """CALC_01 — H / PA across every career head-to-head plate appearance."""
    career = bvp.get("career")
    if career is None:
        return None
    return _rate(career["hits"], career["plateAppearances"])


def calc_02_bvp_season_hit_rate(bvp: dict[str, Any], season: int) -> BvPRate | None:
    """CALC_02 — H / PA head-to-head within *season* only."""
    line = bvp.get("by_season", {}).get(season)
    if line is None:
        return None
    return _rate(line["hits"], line["plateAppearances"])


def calc_03_bvp_recent_window_hit_rate(
    bvp: dict[str, Any], season: int, years: int = RECENT_WINDOW_YEARS
) -> BvPRate | None:
    """CALC_03 — H / PA head-to-head over the last *years* calendar years.

    The window is inclusive of *season*, so the default covers `season - 2`
    through `season`. Summed from per-season splits rather than the API's
    `vsPlayer5Y` stat type, which is fixed at five years.
    """
    line = _season_window(bvp, season, years)
    return _rate(line["hits"], line["plateAppearances"])


def calc_04_bvp_contact_rate(
    bvp: dict[str, Any], season: int | None = None
) -> BvPRate | None:
    """CALC_04 — (PA - SO - BB) / PA head-to-head: how often the matchup ends in contact.

    Career scope by default; pass *season* to restrict to a single season.
    """
    if season is None:
        line = bvp.get("career")
        if line is None:
            return None
    else:
        line = bvp.get("by_season", {}).get(season)
        if line is None:
            return None
    pa = line["plateAppearances"]
    return _rate(pa - line["strikeOuts"] - line["baseOnBalls"], pa)


def compute_bvp_calculators(
    bvp: dict[str, Any], season: int
) -> dict[str, BvPRate | None]:
    """Run every implemented Category 1 calculator over *bvp*.

    CALC_05-08 (hard-hit %, xBA/xwOBA, whiff rate, putaway rate) are absent:
    they require Statcast pitch-level data, which the MLB Stats API does not
    expose per batter-pitcher pair.
    """
    return {
        "CALC_01": calc_01_bvp_career_hit_rate(bvp),
        "CALC_02": calc_02_bvp_season_hit_rate(bvp, season),
        "CALC_03": calc_03_bvp_recent_window_hit_rate(bvp, season),
        "CALC_04": calc_04_bvp_contact_rate(bvp),
    }
