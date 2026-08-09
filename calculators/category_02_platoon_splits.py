"""Category 2 — Platoon & Handedness Splits (`CALC_09`-`CALC_15`).

Pure functions over already-normalized payloads; the network half lives in
`calculators.sources.category_02_platoon_splits`. See `ROADMAP.md` for the
source definition of each `CALC_NN`.

Two payload shapes feed this category:

- **statSplits lines** — ``{"vl": <line>, "vr": <line>}`` where a *line* is a
  dict of `calculators.common.COUNTING_STATS`. Season-scoped, because `sitCodes`
  and date ranges do not compose on the MLB Stats API. Feeds `CALC_09`,
  `CALC_11`, `CALC_13`, `CALC_15`.
- **Statcast pitch records** — one dict per pitch, carrying `game_date`,
  `game_pk`, `at_bat_number`, `events`, `stand`, `p_throws`, `arm_angle`, and the
  expected-stat estimates. Feeds the windowed `CALC_10` and `CALC_12`, and the
  arm-slot `CALC_14`.

Split-code orientation is side-relative and easy to invert by accident:

===========  ==============================  ==============================
code         for a hitter's splits           for a pitcher's splits
===========  ==============================  ==============================
``vl``       vs Left-Handed **Pitchers**     vs Left-Handed **Batters**
``vr``       vs Right-Handed **Pitchers**    vs Right-Handed **Batters**
===========  ==============================  ==============================

**Rates are per plate appearance, not per at-bat.** The ROADMAP words several of
these as "BA", which is H/AB, but the model's foundation defines `p_hit` per
plate appearance and combines it as ``1 - (1 - p_hit)^PA_proj``. Feeding an
H/AB rate into a per-PA exponent would overstate every one of them by roughly
ten percent, since walks leave the denominator. Category 1 already resolved this
the same way (`CALC_01` is H/PA). H/AB remains derivable from the same lines for
anything that genuinely wants batting average.
"""

from __future__ import annotations

from typing import Any, Sequence

from calculators.common import (
    DAYS,
    DELTA,
    HIT_EVENTS,
    MULTIPLIER,
    PROBABILITY,
    Rate,
    Window,
    apply_window,
    rate_or_none,
)

# `HIT_EVENTS` was defined here originally and now lives in `common.py`, since
# Category 3 counts hits off the same pitch-level rows. Still imported into this
# namespace, so `category_02_platoon_splits.HIT_EVENTS` resolves as before.

# Arm-angle bucket boundaries, in degrees. Savant reports 90 as directly
# overhead, 0 as horizontal, and negative for submarine.
#
# Tertile-balanced against a measured sample of 233 MLB pitchers (median 37.0,
# IQR 27.8-43.0, min -61.3, max 68.7 — nobody throws from 90). These split the
# league roughly 28/40/32 percent, so every bucket carries a usable denominator
# and the middle one discriminates.
#
# The physically conventional boundaries (20 and 45) were rejected: they put 70
# percent of pitchers in the middle bucket, which makes CALC_14 measure a hitter
# against nearly the whole league. Re-derive these if the league's slot mix
# shifts; they describe a distribution, not a rule of the game.
ARM_ANGLE_BUCKET_BOUNDS = (30.0, 42.0)

ARM_SLOT_SIDEARM = "sidearm"
ARM_SLOT_THREE_QUARTER = "three_quarter"
ARM_SLOT_OVER_THE_TOP = "over_the_top"


def _opposing_code(hand: str | None) -> str | None:
    """Map a handedness code to the split code describing that hand."""
    if hand == "L":
        return "vl"
    if hand == "R":
        return "vr"
    return None


def _other_code(code: str) -> str:
    return "vr" if code == "vl" else "vl"


def _line_rate(line: dict[str, int] | None) -> Rate | None:
    """Hits per plate appearance for one statSplits line."""
    if not line:
        return None
    return rate_or_none(line.get("hits", 0), line.get("plateAppearances", 0))


# ---------------------------------------------------------------------------
# CALC_09 / CALC_11 — season splits, both sides
# ---------------------------------------------------------------------------


def calc_09_hitter_season_vs_throws(
    splits: dict[str, dict[str, int]], pitcher_throws: str | None
) -> PROBABILITY | None:
    """CALC_09 — hitter's season H/PA against the starter's throwing hand.

    BA only. xBA is not obtainable by handedness from the MLB Stats API:
    `statSplits` carries no expected-stat keys at all, and `expectedStatistics`
    returns them but ignores `sitCodes` entirely, collapsing to one unsplit line.
    Verified 2026-08-08. The expected-stat half of the ROADMAP's "BA/xBA" lives
    on `CALC_10`, which holds Statcast data where those estimates exist.
    """
    code = _opposing_code(pitcher_throws)
    if code is None:
        return None
    value = _line_rate(splits.get(code))
    return PROBABILITY(value) if value is not None else None


def calc_11_pitcher_season_vs_bats(
    splits: dict[str, dict[str, int]], batter_side: str | None
) -> PROBABILITY | None:
    """CALC_11 — starter's season H/PA allowed against the batter's side.

    Depends on the source layer having mapped `battersFaced` onto
    `plateAppearances`: the pitching stat group carries no `plateAppearances`
    key, so without that substitution the denominator is zero and this returns
    None for every pitcher — silently, with nothing raising.

    A switch hitter must have their *current* side resolved before calling; pass
    the side they will actually bat from today, not ``"S"``.
    """
    code = _opposing_code(batter_side)
    if code is None:
        return None
    value = _line_rate(splits.get(code))
    return PROBABILITY(value) if value is not None else None


# ---------------------------------------------------------------------------
# Plate-appearance reconstruction from pitch records
# ---------------------------------------------------------------------------


def _plate_appearances(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse pitch records into one record per plate appearance.

    Grouped on ``(game_pk, at_bat_number)``, which is what actually identifies a
    plate appearance — `at_bat_number` increments once per PA within a game. The
    alternative, counting pitches that carry a terminal `events` value, breaks on
    any pitch where a baserunning event is recorded mid-PA.

    Each returned record carries the PA's `game_date`, its terminal `events`
    value, and every expected-stat reading from batted balls within it, so a
    caller can compute both a per-PA rate and a per-batted-ball average from one
    pass. PAs whose identifying fields are missing are dropped rather than
    merged into a single bogus group.
    """
    grouped: dict[tuple[Any, Any], dict[str, Any]] = {}
    for pitch in pitches:
        game_pk = pitch.get("game_pk")
        at_bat = pitch.get("at_bat_number")
        if game_pk is None or at_bat is None:
            continue
        key = (game_pk, at_bat)
        record = grouped.setdefault(
            key,
            {
                "game_date": pitch.get("game_date"),
                "events": None,
                "xba": [],
                "xwoba": [],
            },
        )
        if pitch.get("events") is not None:
            record["events"] = pitch["events"]
        for field, bucket in (
            ("estimated_ba_using_speedangle", "xba"),
            ("estimated_woba_using_speedangle", "xwoba"),
        ):
            reading = pitch.get(field)
            if reading is not None:
                record[bucket].append(float(reading))
    return list(grouped.values())


def _hit_rate_over_pas(
    pas: Sequence[dict[str, Any]],
) -> tuple[Rate | None, Rate | None]:
    """Return (H/PA, mean xBA) for *pas*.

    Two rates with two different denominators, deliberately not merged: hits are
    counted over plate appearances, while an expected-batting-average estimate
    only exists for balls actually put in play. Sharing one denominator would
    make whichever rate did not own it a lie. Category 1 kept the same pair
    apart for the same reason (`CALC_01` over PAs, `CALC_06` over batted balls).
    """
    if not pas:
        return None, None
    hits = sum(1 for pa in pas if pa.get("events") in HIT_EVENTS)
    hit_rate = rate_or_none(hits, len(pas))

    xba_values = [value for pa in pas for value in pa["xba"]]
    xba = rate_or_none(sum(xba_values), len(xba_values))
    return hit_rate, xba


# ---------------------------------------------------------------------------
# CALC_10 / CALC_12 — 14-day windows, from Statcast
# ---------------------------------------------------------------------------
#
# These come from Statcast rather than the Stats API because `sitCodes` and date
# ranges do not compose there. Verified 2026-08-08: `byDateRange` honored the
# window and dropped the split (code=None), while `statSplits` honored the split
# and returned the identical full-season line regardless of the date range.


def calc_10_hitter_recent_vs_throws(
    pitches: Sequence[dict[str, Any]],
    pitcher_throws: str | None,
    window: Window | None = None,
    **window_kwargs: Any,
) -> dict[str, PROBABILITY | None]:
    """CALC_10 — hitter's recent H/PA and xBA against the starter's throwing hand.

    Emits two keys, following the `CALC_06_XBA` / `CALC_06_XWOBA` precedent: the
    ROADMAP asks for "BA/xBA", and `estimated_ba_using_speedangle` is already in
    the frame this calculator holds. They carry separate denominators — see
    `_hit_rate_over_pas`.
    """
    if pitcher_throws not in ("L", "R"):
        return {"CALC_10": None, "CALC_10_XBA": None}
    matching = [p for p in pitches if p.get("p_throws") == pitcher_throws]
    windowed = apply_window(matching, window or DAYS(14), **window_kwargs)
    if windowed is None:
        return {"CALC_10": None, "CALC_10_XBA": None}

    hit_rate, xba = _hit_rate_over_pas(_plate_appearances(windowed))
    return {
        "CALC_10": PROBABILITY(hit_rate) if hit_rate is not None else None,
        "CALC_10_XBA": PROBABILITY(xba) if xba is not None else None,
    }


def calc_12_pitcher_recent_vs_bats(
    pitches: Sequence[dict[str, Any]],
    batter_side: str | None,
    window: Window | None = None,
    **window_kwargs: Any,
) -> dict[str, PROBABILITY | None]:
    """CALC_12 — starter's recent H/PA and xBA allowed against the batter's side."""
    if batter_side not in ("L", "R"):
        return {"CALC_12": None, "CALC_12_XBA": None}
    matching = [p for p in pitches if p.get("stand") == batter_side]
    windowed = apply_window(matching, window or DAYS(14), **window_kwargs)
    if windowed is None:
        return {"CALC_12": None, "CALC_12_XBA": None}

    hit_rate, xba = _hit_rate_over_pas(_plate_appearances(windowed))
    return {
        "CALC_12": PROBABILITY(hit_rate) if hit_rate is not None else None,
        "CALC_12_XBA": PROBABILITY(xba) if xba is not None else None,
    }


# ---------------------------------------------------------------------------
# CALC_13 — switch-hitter split acuity
# ---------------------------------------------------------------------------


def calc_13_switch_hitter_split_acuity(
    splits: dict[str, dict[str, int]],
    batter_side: str | None,
    pitcher_throws: str | None,
) -> DELTA | None:
    """CALC_13 — rate from today's forced side minus rate from the other side.

    Returns None for anyone who is not a switch hitter: a player with one side
    has no other side to differ from, and reporting 0.0 would claim the two were
    equal. Only ``batSide`` ``"S"`` qualifies.

    A switch hitter's side is determined by the pitcher — he bats left against a
    right-hander — so "the side he bats today" and "the hand he faces today" are
    the same fact, and the differential reads straight off the two splits.

    Negative means today's forced side is his weaker one. The value is a signed
    difference of two rates, so it is not clamped to [0, 1]; the role is `DELTA`
    precisely so a consumer cannot mistake it for a probability.

    The denominator carried is the *smaller* of the two sides' samples. A
    differential is only as trustworthy as its thinner half, and for switch
    hitters that half is structurally thin — the off-side sample is the one a
    platoon-savvy manager spends all season avoiding.
    """
    if batter_side != "S":
        return None
    today_code = _opposing_code(pitcher_throws)
    if today_code is None:
        return None

    today = _line_rate(splits.get(today_code))
    other = _line_rate(splits.get(_other_code(today_code)))
    if today is None or other is None:
        return None
    return DELTA(
        Rate(today.rate - other.rate, min(today.denominator, other.denominator))
    )


# ---------------------------------------------------------------------------
# CALC_14 — arm slot / release angle match
# ---------------------------------------------------------------------------


def arm_slot_bucket(arm_angle: float | None) -> str | None:
    """Bucket an arm angle into sidearm / three-quarter / over-the-top.

    A negative angle is submarine, which belongs in the lowest bucket — not
    treated as invalid. Returns None only when the reading is absent.
    """
    if arm_angle is None:
        return None
    low, high = ARM_ANGLE_BUCKET_BOUNDS
    if arm_angle < low:
        return ARM_SLOT_SIDEARM
    if arm_angle < high:
        return ARM_SLOT_THREE_QUARTER
    return ARM_SLOT_OVER_THE_TOP


def calc_14_arm_slot_match(
    pitches: Sequence[dict[str, Any]], starter_arm_angle: float | None
) -> PROBABILITY | None:
    """CALC_14 — hitter's H/PA against pitchers in the starter's arm-slot bucket.

    Averages over the *hitter's* pitches, not the starter's: the question is how
    this hitter fares against that class of delivery, which is answerable even
    when he has never faced this particular pitcher.

    Pitches with no `arm_angle` reading are excluded from the denominator rather
    than defaulted — the column only exists from 2024, and a missing reading is
    not a slot.

    **Season-scoped**, like `CALC_05`-`CALC_08`: the source pulls one season of
    pitches per call. So the breadth this answers over is every pitcher of that
    slot the hitter faced *this year*, not his career against the slot. That is
    still far wider than one matchup, which is the point, but it is not the
    unbounded history the phrase "that class of delivery" might suggest.
    """
    bucket = arm_slot_bucket(starter_arm_angle)
    if bucket is None:
        return None
    matching = [p for p in pitches if arm_slot_bucket(p.get("arm_angle")) == bucket]
    if not matching:
        return None
    hit_rate, _ = _hit_rate_over_pas(_plate_appearances(matching))
    return PROBABILITY(hit_rate) if hit_rate is not None else None


# ---------------------------------------------------------------------------
# CALC_15 — reverse platoon split index
# ---------------------------------------------------------------------------


def calc_15_reverse_platoon_index(
    splits: dict[str, dict[str, int]],
    batter_side: str | None,
    pitcher_throws: str | None,
    league_baseline: dict[str, dict[str, Any]],
) -> MULTIPLIER | None:
    """CALC_15 — how far this hitter's platoon behavior runs against the grain.

    Compares the hitter's own edge in today's matchup against the edge the
    league's hitters of his hand show in the same matchup::

        own_edge    = own_rate(hand faced today)    - own_rate(other hand)
        league_edge = league(his hand vs today)     - league(his hand vs other)
        index       = 1 + (own_edge - league_edge)

    Centered on 1.0, and continuous rather than a flag: a hitter whose splits are
    exactly league-typical scores 1.0 regardless of which hand he faces, a
    conventional hitter facing his advantage side scores above it, and a
    reverse-split hitter in that same matchup scores below. A mild reverse split
    lands nearer 1.0 than an extreme one — there is no threshold at which one
    plate appearance flips the output by a fixed constant.

    Switch hitters return None: they have no fixed hand for the league baseline
    to be keyed on, and `CALC_13` is the calculator that describes them.

    Role is `MULTIPLIER` — this scales `p_hit`, and must never be read as a
    probability. Denominator is the limiting side, as in `CALC_13`.
    """
    if batter_side not in ("L", "R") or pitcher_throws not in ("L", "R"):
        return None
    if not league_baseline:
        return None

    today_code = _opposing_code(pitcher_throws)
    other_hand = "R" if pitcher_throws == "L" else "L"
    today = _line_rate(splits.get(today_code))
    other = _line_rate(splits.get(_other_code(today_code)))
    if today is None or other is None:
        return None

    league_today = league_baseline.get(f"{batter_side}_vs_{pitcher_throws}", {})
    league_other = league_baseline.get(f"{batter_side}_vs_{other_hand}", {})
    if league_today.get("rate") is None or league_other.get("rate") is None:
        return None

    own_edge = today.rate - other.rate
    league_edge = league_today["rate"] - league_other["rate"]
    return MULTIPLIER(
        Rate(
            1.0 + (own_edge - league_edge),
            min(today.denominator, other.denominator),
        )
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_02(
    hitter_splits: dict[str, dict[str, int]] | None = None,
    pitcher_splits: dict[str, dict[str, int]] | None = None,
    hitter_pitches: Sequence[dict[str, Any]] | None = None,
    pitcher_pitches: Sequence[dict[str, Any]] | None = None,
    batter_side: str | None = None,
    pitcher_throws: str | None = None,
    starter_arm_angle: float | None = None,
    league_baseline: dict[str, dict[str, Any]] | None = None,
    **window_kwargs: Any,
) -> dict[str, Any]:
    """Run every implemented Category 2 calculator.

    Absent inputs resolve every affected key to ``None`` rather than omitting it,
    matching `compute_category_01`'s contract so a consumer can rely on the key
    set without knowing what was fetched.

    `batter_side` is the side the hitter will actually bat from today. For a
    switch hitter pass ``"S"``: `CALC_13` needs it to identify him, and the
    calculators that need a concrete side resolve it from the pitcher's hand.
    """
    hitter_splits = hitter_splits or {}
    pitcher_splits = pitcher_splits or {}
    hitter_pitches = hitter_pitches or []
    pitcher_pitches = pitcher_pitches or []
    league_baseline = league_baseline or {}

    # A switch hitter's effective side is the opposite of the hand he faces.
    if batter_side == "S" and pitcher_throws in ("L", "R"):
        effective_side = "L" if pitcher_throws == "R" else "R"
    else:
        effective_side = batter_side

    results: dict[str, Any] = {
        "CALC_09": calc_09_hitter_season_vs_throws(hitter_splits, pitcher_throws),
        "CALC_11": calc_11_pitcher_season_vs_bats(pitcher_splits, effective_side),
        "CALC_13": calc_13_switch_hitter_split_acuity(
            hitter_splits, batter_side, pitcher_throws
        ),
        "CALC_14": calc_14_arm_slot_match(hitter_pitches, starter_arm_angle),
        "CALC_15": calc_15_reverse_platoon_index(
            hitter_splits, batter_side, pitcher_throws, league_baseline
        ),
    }
    results.update(
        calc_10_hitter_recent_vs_throws(hitter_pitches, pitcher_throws, **window_kwargs)
    )
    results.update(
        calc_12_pitcher_recent_vs_bats(pitcher_pitches, effective_side, **window_kwargs)
    )
    return results
