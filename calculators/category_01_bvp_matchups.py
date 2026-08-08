"""Category 1 — Direct Pitcher vs. Batter (BvP) Matchups (`CALC_01`-`CALC_08`).

Pure functions over an already-normalized BvP payload; the network half lives in
`calculators.sources`. See `ROADMAP.md` for the source definition of each
`CALC_NN`.

The payload is produced by `parse_bvp_stats` and has the shape::

    {"career": <line> | None, "by_season": {2026: <line>, ...}}

where a *line* is a dict of `calculators.common.COUNTING_STATS`.

`CALC_05`-`CALC_08` work from Statcast pitch-level data instead, supplied by
`calculators.sources.fetch_bvp_statcast` as a list of normalized pitch records —
one dict per pitch the batter saw from that pitcher, with `None` (never NaN) for
absent readings. Statcast only goes back to 2015 and a fetch pulls one season at
a time, so these are season-scoped, unlike `CALC_01`'s true career window. The
scope is a parameter on the fetch, not a constant here.

`CALC_06` is the one ROADMAP consideration carrying two outputs (xBA and xwOBA);
it is emitted under two keys, `CALC_06_XBA` and `CALC_06_XWOBA`.
"""

from typing import Any, Sequence

from calculators.common import (
    COUNTING_STATS,
    Rate,
    aggregate_lines,
    rate_or_none,
)


# CALC_03's window: the current season plus the two prior calendar years.
RECENT_WINDOW_YEARS = 3

# Statcast `description` value for a pitch the batter put into play.
IN_PLAY = "hit_into_play"

# Statcast `description` values that mean the batter swung. Anything the bat
# made contact with counts, fouls included — the denominator of CALC_07 is
# "total swings", not "swings that could have been hits".
SWING_DESCRIPTIONS = frozenset(
    {
        "swinging_strike",
        "swinging_strike_blocked",
        "foul",
        "foul_tip",
        "foul_bunt",
        "bunt_foul_tip",
        "missed_bunt",
        IN_PLAY,
    }
)

# Swings where the bat missed the ball entirely. `foul_tip` and `foul_bunt` are
# deliberately excluded: ROADMAP defines CALC_07 as "swings and misses", and a
# tipped ball is contact even though it is scored a strike.
WHIFF_DESCRIPTIONS = frozenset(
    {
        "swinging_strike",
        "swinging_strike_blocked",
        "missed_bunt",
    }
)

# Statcast `events` values that end the plate appearance in a strikeout.
STRIKEOUT_EVENTS = frozenset({"strikeout", "strikeout_double_play"})

# Exit velocity at or above which a batted ball is "hard hit", per Statcast.
HARD_HIT_MPH = 95.0


def parse_bvp_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw `stats=vsPlayer` response into the category's payload.

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


def _season_window(bvp: dict[str, Any], season: int, years: int) -> dict[str, int]:
    """Aggregate the seasons in the inclusive window ending at *season*."""
    earliest = season - years + 1
    return aggregate_lines(
        line
        for year, line in bvp.get("by_season", {}).items()
        if earliest <= year <= season
    )


def calc_01_bvp_career_hit_rate(bvp: dict[str, Any]) -> Rate | None:
    """CALC_01 — H / PA across every career head-to-head plate appearance."""
    career = bvp.get("career")
    if career is None:
        return None
    return rate_or_none(career["hits"], career["plateAppearances"])


def calc_02_bvp_season_hit_rate(bvp: dict[str, Any], season: int) -> Rate | None:
    """CALC_02 — H / PA head-to-head within *season* only."""
    line = bvp.get("by_season", {}).get(season)
    if line is None:
        return None
    return rate_or_none(line["hits"], line["plateAppearances"])


def calc_03_bvp_recent_window_hit_rate(
    bvp: dict[str, Any], season: int, years: int = RECENT_WINDOW_YEARS
) -> Rate | None:
    """CALC_03 — H / PA head-to-head over the last *years* calendar years.

    The window is inclusive of *season*, so the default covers `season - 2`
    through `season`. Summed from per-season splits rather than the API's
    `vsPlayer5Y` stat type, which is fixed at five years.
    """
    line = _season_window(bvp, season, years)
    return rate_or_none(line["hits"], line["plateAppearances"])


def calc_04_bvp_contact_rate(
    bvp: dict[str, Any], season: int | None = None
) -> Rate | None:
    """CALC_04 — (PA - SO - BB) / PA head-to-head: how often the matchup ends in contact.

    Career scope by default; pass *season* to restrict to a single season.
    """
    if season is None:
        line = bvp.get("career")
    else:
        line = bvp.get("by_season", {}).get(season)
    if line is None:
        return None
    pa = line["plateAppearances"]
    return rate_or_none(pa - line["strikeOuts"] - line["baseOnBalls"], pa)


def _batted_balls(pitches: Sequence[dict[str, Any]], field: str) -> list[float]:
    """Return *field* for every batted ball that carries a reading for it.

    Statcast leaves exit velocity and the expected-stat estimates unset on some
    batted balls, and a missing reading is not a zero — it has to leave the
    denominator rather than drag the average down.
    """
    values = []
    for pitch in pitches:
        if pitch.get("description") != IN_PLAY:
            continue
        value = pitch.get(field)
        if value is not None:
            values.append(float(value))
    return values


def calc_05_bvp_hard_hit_rate(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """CALC_05 — share of batted balls hit at or above 95 mph exit velocity."""
    speeds = _batted_balls(pitches, "launch_speed")
    hard = sum(1 for speed in speeds if speed >= HARD_HIT_MPH)
    return rate_or_none(hard, len(speeds))


def calc_06_bvp_xba(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """CALC_06 (xBA) — mean expected batting average on contact.

    The `rate` is an average of per-batted-ball estimates rather than a ratio of
    counts, but it is still a value over a sample size, so it carries the same
    `Rate` shape as everything else.
    """
    values = _batted_balls(pitches, "estimated_ba_using_speedangle")
    return rate_or_none(sum(values), len(values))


def calc_06_bvp_xwoba(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """CALC_06 (xwOBA) — mean expected weighted on-base average on contact."""
    values = _batted_balls(pitches, "estimated_woba_using_speedangle")
    return rate_or_none(sum(values), len(values))


def calc_07_bvp_whiff_rate(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """CALC_07 — swings and misses / total swings.

    Swing and whiff classification is by Statcast `description`; see
    `SWING_DESCRIPTIONS` and `WHIFF_DESCRIPTIONS` for the exact membership.
    """
    swings = [p for p in pitches if p.get("description") in SWING_DESCRIPTIONS]
    whiffs = sum(1 for p in swings if p["description"] in WHIFF_DESCRIPTIONS)
    return rate_or_none(whiffs, len(swings))


def calc_08_bvp_putaway_rate(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """CALC_08 — strikeouts / pitches thrown in two-strike counts.

    The denominator is two-strike *pitches*, not two-strike plate appearances:
    a batter who fouls off six pitches before striking out survived six chances
    to be put away, and the rate should reflect that.
    """
    two_strike = [p for p in pitches if p.get("strikes") == 2]
    putaways = sum(1 for p in two_strike if p.get("events") in STRIKEOUT_EVENTS)
    return rate_or_none(putaways, len(two_strike))


def compute_category_01(
    bvp: dict[str, Any],
    season: int,
    pitches: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Rate | None]:
    """Run every implemented Category 1 calculator.

    *bvp* feeds CALC_01-04. *pitches* feeds the Statcast-derived CALC_05-08; pass
    ``None`` when no Statcast data was fetched and those keys resolve to ``None``
    rather than being omitted.
    """
    pitches = pitches or []
    return {
        "CALC_01": calc_01_bvp_career_hit_rate(bvp),
        "CALC_02": calc_02_bvp_season_hit_rate(bvp, season),
        "CALC_03": calc_03_bvp_recent_window_hit_rate(bvp, season),
        "CALC_04": calc_04_bvp_contact_rate(bvp),
        "CALC_05": calc_05_bvp_hard_hit_rate(pitches),
        "CALC_06_XBA": calc_06_bvp_xba(pitches),
        "CALC_06_XWOBA": calc_06_bvp_xwoba(pitches),
        "CALC_07": calc_07_bvp_whiff_rate(pitches),
        "CALC_08": calc_08_bvp_putaway_rate(pitches),
    }
