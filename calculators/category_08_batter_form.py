"""Category 8: Batter Trending Form & Quality of Contact (`CALC_52`-`CALC_59`).

Pure functions over already-normalized pitch records; the network half lives in
`calculators.sources.category_08_batter_form`. See `ROADMAP.md` for the source
definition of each `CALC_NN`.

**The first single-sided category.** Categories 1 through 4 all ask how a hitter
fares against some property of today's starter. Category 8 asks only how the
hitter is going, so every calculator takes one argument. Nothing about the
opposing pitcher enters.

**Every calculator accepts `today`.** The recency windows are all anchored to a
date, and a calculator that reads the clock internally cannot be tested without
freezing it and cannot be evaluated against a past date at all, which is exactly
what the validation harness in #35 will need to do. `today` threads straight
through to `common.apply_window`, whose `DAYS(n)` window ends *yesterday*, since
today's game has not been played yet.

**Spring training is excluded, and this category is the only one that does it.**
A Statcast season pull includes spring games. At season aggregate that is noise;
across a three-game or seven-day window in late March it is most of the sample.
Statcast also computes no expected statistics for spring games, so keeping them
would put plate appearances into a denominator whose expected-stat numerator
silently vanishes. Filtering flipped the probe hitter's `CALC_57` from +0.0043 to
-0.0183, a sign change. Categories 1 through 4 do **not** filter and are computed
over samples that are 8 to 10 percent spring training; that is a real defect in
shipped code, not a stylistic difference, and it is drafted for filing rather
than silently repaired here.

**Rates are per plate appearance**, like every other category, because the
model's foundation defines `p_hit` per plate appearance and combines it as
``1 - (1 - p_hit)^PA_proj``. This is why `CALC_52` is not the "hit in the last 3
games, yes or no" frequency the ROADMAP's shorthand suggests: a per-game hit
frequency is already on the *output* side of that formula, and feeding it back in
as a `p_hit` input would apply the binomial twice.

**Role tags follow Category 4's stricter convention, not Category 1's.**
`PROBABILITY` is reserved for the three genuine per-plate-appearance hit rates.
Hard-hit rate, sweet-spot rate, xwOBA, and BABIP are `MULTIPLIER`: real rates,
but not on the `p_hit` scale and not safe to blend into it directly. Category 1
tags its hard-hit rate and xwOBA `PROBABILITY` "for uniformity" with a docstring
warning instead. The two conventions disagree and Category 1's is the weaker one,
since it puts a .56 hard-hit rate and a .21 hit rate under the same tag.

**Nothing here is wired into a run.** The calculators are built and tested in
isolation and get consumed compositely in a later release; the ranked table is
untouched and no Category 8 request is made during a daily run.
"""

from __future__ import annotations

import datetime
from typing import Any, Sequence

from calculators.common import (
    DAYS,
    DELTA,
    HARD_HIT_MPH,
    HIT_EVENTS,
    IN_PLAY,
    MULTIPLIER,
    PLATE_APPEARANCES,
    PROBABILITY,
    Rate,
    apply_window,
    is_competitive,
    rate_or_none,
    terminal_pitch_by_pa,
)

# Launch-angle band Statcast calls the "sweet spot", in degrees, inclusive at
# both ends. `CALC_59` measures how often the hitter finds it.
SWEET_SPOT_ANGLE_DEGREES = (8.0, 32.0)

# Events excluded from a BABIP denominator even though the ball was put in play.
# Home runs leave the field and no defense could have caught them, which is the
# whole premise of the statistic. Sacrifice bunts are excluded by the classic
# formula as a deliberate act rather than an outcome; none appeared in the probe
# sample, so that half of this set is implemented from the definition and not
# verified against data.
BABIP_EXCLUDED_EVENTS = frozenset({"home_run", "sac_bunt"})


# ---------------------------------------------------------------------------
# Reductions
# ---------------------------------------------------------------------------


def plate_appearances(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce *pitches* to one competitive plate-appearance record each.

    The entry point for every calculator in the category. Two steps, and the
    order matters: reduce each plate appearance to its terminal pitch, then drop
    the non-competitive games. Doing it the other way would work here only
    because `game_type` is constant within a plate appearance, and relying on
    that is the habit that produced the CALC_14 defect in issue #37.

    Sorted by date ascending so every consumer sees the same ordering, since
    `apply_window`'s plate-appearance window and the hit-streak walk both depend
    on it.
    """
    terminal = [p for p in terminal_pitch_by_pa(pitches) if is_competitive(p)]
    return sorted(
        terminal, key=lambda p: (p.get("game_date") or "", p.get("game_pk") or 0)
    )


def game_log(pas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate plate appearances into one record per game, oldest first.

    Keyed on ``game_pk``, not on ``game_date``, so the two halves of a
    doubleheader stay two games. This is why `CALC_52` and `CALC_56` do not route
    through ``apply_window(GAMES(n))``: that helper dedups by date string, which
    collapses a doubleheader into one game. For a season aggregate the difference
    is invisible; for a three-game window it silently widens the sample by a day.
    """
    by_game: dict[Any, dict[str, Any]] = {}
    for pa in pas:
        key = pa.get("game_pk")
        if key is None:
            continue
        record = by_game.setdefault(
            key,
            {"game_pk": key, "game_date": pa.get("game_date"), "hits": 0, "pa": 0},
        )
        record["pa"] += 1
        if pa.get("events") in HIT_EVENTS:
            record["hits"] += 1
    return sorted(by_game.values(), key=lambda g: (g["game_date"] or "", g["game_pk"]))


def _hit_rate(pas: Sequence[dict[str, Any]]) -> Rate | None:
    """Hits per plate appearance."""
    if not pas:
        return None
    hits = sum(1 for pa in pas if pa.get("events") in HIT_EVENTS)
    return rate_or_none(hits, len(pas))


def _batted_balls(pas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Plate appearances that ended with the ball put in play."""
    return [pa for pa in pas if pa.get("description") == IN_PLAY]


def _mean_reading(pas: Sequence[dict[str, Any]], field: str) -> Rate | None:
    """Mean of *field* over the batted balls that carry a reading.

    The denominator is batted balls carrying a reading **of that field**, not all
    batted balls and not plate appearances. A null is no reading rather than a
    zero, so it leaves both numerator and denominator.

    Per field on purpose: the expected-stat columns go null independently of each
    other. In the probe frame's 143 competitive batted balls,
    `estimated_ba_using_speedangle` was null once and
    `estimated_woba_using_speedangle` never was. A shared "measurable batted ball"
    filter would therefore throw away a good xwOBA reading to match a missing xBA
    one, and two calculators reading different columns can legitimately report
    different sample sizes over the same plate appearances.
    """
    values = [
        float(pa[field]) for pa in _batted_balls(pas) if pa.get(field) is not None
    ]
    return rate_or_none(sum(values), len(values))


def _windowed(
    pas: Sequence[dict[str, Any]],
    window,
    today: datetime.date | None,
) -> list[dict[str, Any]] | None:
    """Slice *pas* to *window*, or None when the window holds nothing."""
    return apply_window(pas, window, today=today)


def _probability(rate: Rate | None) -> PROBABILITY | None:
    return PROBABILITY(rate) if rate is not None else None


def _multiplier(rate: Rate | None) -> MULTIPLIER | None:
    return MULTIPLIER(rate) if rate is not None else None


# ---------------------------------------------------------------------------
# CALC_52 -- last-N-games form
# ---------------------------------------------------------------------------


def calc_52_recent_game_form(
    pitches: Sequence[dict[str, Any]],
    games: int = 3,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_52: hit rate over the last *games* games, and multi-hit frequency.

    `CALC_52` is hits per plate appearance across those games, on the same
    `p_hit` scale as every other `PROBABILITY` in the model.

    The ROADMAP writes this as "hit recorded in last 3 games (Y/N)", and that
    reading is deliberately **not** the primary value. The share of games with at
    least one hit is already the quantity the model predicts, the left-hand side
    of ``1 - (1 - p_hit)^PA_proj``. Feeding it back in as a `p_hit` input would
    put the binomial through twice.

    `CALC_52_MULTI_HIT` carries the ROADMAP's second request, the share of those
    games with two or more hits. `MULTIPLIER`, not `PROBABILITY`, for the same
    reason: it is a per-game frequency, not a per-plate-appearance rate, and the
    two must not be blended as though they were the same quantity.

    *today* is accepted and unused: the window here is game-anchored, so it is
    unaffected by the calendar. It stays in the signature so every Category 8
    calculator can be called through one uniform interface.
    """
    log = game_log(plate_appearances(pitches))[-games:]
    if not log:
        return {"CALC_52": None, "CALC_52_MULTI_HIT": None}

    total_pa = sum(g["pa"] for g in log)
    total_hits = sum(g["hits"] for g in log)
    multi = sum(1 for g in log if g["hits"] >= 2)
    return {
        "CALC_52": _probability(rate_or_none(total_hits, total_pa)),
        "CALC_52_MULTI_HIT": _multiplier(rate_or_none(multi, len(log))),
    }


# ---------------------------------------------------------------------------
# CALC_53 / CALC_54 -- calendar-day hit rates
# ---------------------------------------------------------------------------
#
# Both are `DAYS(n)`, not `GAMES(n)`, and the difference is the ROADMAP's own:
# CALC_52 says "3-Game" while CALC_53 and CALC_54 say "last 7 calendar days" and
# "last 14 calendar days". A calendar window shrinks when the hitter sits or the
# schedule has an off-day, and that is the intended behavior. A player who missed
# a week returns a thinner sample rather than one reaching further back, which is
# the honest answer about his recent form.


def calc_53_seven_day_hit_rate(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> PROBABILITY | None:
    """CALC_53: hits per plate appearance over the last 7 calendar days."""
    windowed = _windowed(plate_appearances(pitches), DAYS(7), today)
    return _probability(_hit_rate(windowed or []))


def calc_54_fourteen_day_hit_rate(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> PROBABILITY | None:
    """CALC_54: hits per plate appearance over the last 14 calendar days."""
    windowed = _windowed(plate_appearances(pitches), DAYS(14), today)
    return _probability(_hit_rate(windowed or []))


# ---------------------------------------------------------------------------
# CALC_55 -- luck-neutralized contact quality
# ---------------------------------------------------------------------------


def calc_55_contact_quality_trend(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_55: xwOBA and hard-hit rate on contact over the last 14 days.

    Two keys rather than one, following the `CALC_06_XBA` / `CALC_06_XWOBA`
    precedent: the ROADMAP names both quantities and neither is derivable from
    the other.

    Both are per **batted ball**, and both are `MULTIPLIER`. xwOBA is not bounded
    by 1.0 at all, since wOBA weights extra-base hits above singles; a hard-hit
    rate runs near .40 league-wide. Neither belongs anywhere near a `p_hit` blend
    without the mapping issue #39 owes.

    This is the "luck-neutralized" leg of the category. `CALC_53` and `CALC_54`
    report what happened, which over two weeks is mostly batted-ball luck;
    these report how hard the ball was struck, which stabilizes far faster.
    """
    windowed = _windowed(plate_appearances(pitches), DAYS(14), today) or []
    hard = [pa for pa in _batted_balls(windowed) if pa.get("launch_speed") is not None]
    hard_rate = rate_or_none(
        sum(1 for pa in hard if float(pa["launch_speed"]) >= HARD_HIT_MPH), len(hard)
    )
    return {
        "CALC_55_XWOBA": _multiplier(
            _mean_reading(windowed, "estimated_woba_using_speedangle")
        ),
        "CALC_55_HARD_HIT": _multiplier(hard_rate),
    }


# ---------------------------------------------------------------------------
# CALC_56 -- active hit streak
# ---------------------------------------------------------------------------


def calc_56_active_hit_streak(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> DELTA | None:
    """CALC_56: consecutive most-recent games with at least one hit.

    Units are **games**, not a rate, which is why the role is `DELTA` following
    the `CALC_19` (runs per 100 pitches) and `CALC_23_VELO_GAIN` (mph) precedent.
    The `Rate`'s denominator carries the number of games examined, so a streak of
    3 out of 3 games on record is distinguishable from 3 out of 60.

    Not expressible through `apply_window`: a streak is not a window but a walk
    backward from the most recent game until the condition breaks, and its length
    is the answer rather than the slice.

    A hitless most-recent game returns a streak of **0**, which is a real
    measurement. Only an empty game log returns None, keeping "no games on
    record" distinct from "the streak just ended".

    *today* is accepted and unused; the walk is anchored to the most recent game
    on record, not to the calendar.
    """
    log = game_log(plate_appearances(pitches))
    if not log:
        return None
    streak = 0
    for game in reversed(log):
        if game["hits"] < 1:
            break
        streak += 1
    return DELTA(Rate(float(streak), len(log)))


# ---------------------------------------------------------------------------
# CALC_57 -- luck delta
# ---------------------------------------------------------------------------


def calc_57_luck_delta(
    pitches: Sequence[dict[str, Any]],
    window=None,
    today: datetime.date | None = None,
) -> DELTA | None:
    """CALC_57: actual hit rate minus expected hit rate, per plate appearance.

    Negative means the hitter has been unlucky and is a positive regression
    candidate, which is the ROADMAP's "high xBA + low BA" case.

    **Both terms share one denominator, and getting that wrong is the whole
    trap.** An xBA estimate exists only on batted balls, so the natural mean xBA
    runs around .41 while a hit rate runs around .21. Subtracting one from the
    other yields roughly -.20 for every hitter alive, a number that looks like a
    catastrophic slump and is actually a units error. Here both legs divide by the
    same plate appearances: actual is hits over plate appearances, expected is the
    *sum* of per-batted-ball xBA over the same plate appearances. A strikeout
    contributes 0 to both numerators and 1 to the shared denominator, which is
    correct, since a strikeout really is zero expected hits.

    Plate appearances whose batted ball carries no xBA estimate are dropped from
    both legs rather than counted as zero expected hits. Statcast misses roughly
    1 batted ball in 150 in the regular season, and a missed estimate on a hit
    would manufacture the appearance of good luck. This is a small correction only
    because spring training is already excluded upstream: unfiltered, 13 of the
    probe frame's 14 unestimated batted balls were spring games, 3 of them home
    runs, and the pair of corrections together moved the delta from +0.0043 to
    -0.0183.

    *window* defaults to the whole pull, which is one season. Pass a `Window` for
    a recency-scoped slump reading.
    """
    pas = plate_appearances(pitches)
    if window is not None:
        pas = _windowed(pas, window, today) or []

    measurable = [
        pa
        for pa in pas
        if pa.get("description") != IN_PLAY
        or pa.get("estimated_ba_using_speedangle") is not None
    ]
    if not measurable:
        return None

    hits = sum(1 for pa in measurable if pa.get("events") in HIT_EVENTS)
    expected = sum(
        float(pa["estimated_ba_using_speedangle"])
        for pa in _batted_balls(measurable)
        if pa.get("estimated_ba_using_speedangle") is not None
    )
    return DELTA(Rate((hits - expected) / len(measurable), len(measurable)))


# ---------------------------------------------------------------------------
# CALC_58 -- BABIP regression vector
# ---------------------------------------------------------------------------


def babip(pas: Sequence[dict[str, Any]]) -> Rate | None:
    """Batting average on balls in play.

    Hits excluding home runs, over balls put in play excluding home runs and
    sacrifice bunts. Balls in play are identified by the terminal pitch's
    ``description == "hit_into_play"`` rather than reconstructed from the event
    vocabulary, which is the same identification Category 4 uses.

    Deliberately *not* per plate appearance, unlike the rest of the category.
    BABIP is a named statistic with a fixed denominator, and the point of
    `CALC_58` is comparing a hitter's recent value against his own baseline; both
    legs must be the real statistic for the comparison to mean anything.
    """
    in_play = [
        pa for pa in _batted_balls(pas) if pa.get("events") not in BABIP_EXCLUDED_EVENTS
    ]
    if not in_play:
        return None
    hits = sum(1 for pa in in_play if pa.get("events") in HIT_EVENTS)
    return rate_or_none(hits, len(in_play))


def calc_58_babip_regression(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_58: recent BABIP against the hitter's own season baseline.

    Three keys. `CALC_58` is the last 14 days, `CALC_58_SEASON` is the whole
    pull, and `CALC_58_DELTA` is recent minus season, the regression vector the
    ROADMAP asks for. A large positive delta means recent results have outrun the
    hitter's own baseline and should be expected to fall back.

    The baseline is a **season** baseline, not the career one the ROADMAP also
    mentions. The source fetcher is season-scoped, so a career BABIP would need a
    multi-season pull that does not exist yet; it is left undone rather than
    approximated from a single year.

    `CALC_58_DELTA` is None whenever either leg is, since a difference against a
    missing baseline is not a small number, it is no number.
    """
    pas = plate_appearances(pitches)
    recent = babip(_windowed(pas, DAYS(14), today) or [])
    season = babip(pas)

    delta = None
    if recent is not None and season is not None:
        delta = DELTA(Rate(recent.rate - season.rate, recent.denominator))

    return {
        "CALC_58": _multiplier(recent),
        "CALC_58_SEASON": _multiplier(season),
        "CALC_58_DELTA": delta,
    }


# ---------------------------------------------------------------------------
# CALC_59 -- sweet-spot trend
# ---------------------------------------------------------------------------


def calc_59_sweet_spot_trend(
    pitches: Sequence[dict[str, Any]],
    window_size: int = 30,
    today: datetime.date | None = None,
) -> MULTIPLIER | None:
    """CALC_59: share of recent batted balls struck in the sweet-spot launch band.

    The window is the last *window_size* **plate appearances**, per the ROADMAP,
    while the rate itself is per batted ball. Those are different denominators on
    purpose: 30 plate appearances is the scope, and a launch angle only exists
    where the ball was struck, so 30 plate appearances typically yields 15 to 20
    measurements.

    `apply_window`'s `PLATE_APPEARANCES(n)` requires one record per plate
    appearance, which is what `plate_appearances` produces. Handing it raw pitch
    rows would silently window the last 30 *pitches*, roughly 8 plate
    appearances, and return a plausible number over a quarter of the intended
    sample.
    """
    windowed = _windowed(
        plate_appearances(pitches), PLATE_APPEARANCES(window_size), today
    )
    angles = [
        float(pa["launch_angle"])
        for pa in _batted_balls(windowed or [])
        if pa.get("launch_angle") is not None
    ]
    low, high = SWEET_SPOT_ANGLE_DEGREES
    return _multiplier(
        rate_or_none(sum(1 for a in angles if low <= a <= high), len(angles))
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_08(
    pitches: Sequence[dict[str, Any]] | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Run every Category 8 calculator.

    Absent input resolves every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_04` so a
    consumer can rely on the key set without knowing what was fetched.

    *pitches* is every pitch the batter saw this season, from
    `calculators.sources.category_08_batter_form.fetch_batter_form`. *today*
    anchors every recency window and is threaded to all of them, so an evaluation
    against a past date needs no clock patching.
    """
    pitches = pitches or []
    results: dict[str, Any] = {
        "CALC_53": calc_53_seven_day_hit_rate(pitches, today=today),
        "CALC_54": calc_54_fourteen_day_hit_rate(pitches, today=today),
        "CALC_56": calc_56_active_hit_streak(pitches, today=today),
        "CALC_57": calc_57_luck_delta(pitches, today=today),
        "CALC_59": calc_59_sweet_spot_trend(pitches, today=today),
    }
    for calculator in (
        calc_52_recent_game_form,
        calc_55_contact_quality_trend,
        calc_58_babip_regression,
    ):
        results.update(calculator(pitches, today=today))
    return results
