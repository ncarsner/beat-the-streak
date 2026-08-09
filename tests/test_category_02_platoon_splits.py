"""Tests for calculators/category_02_platoon_splits.py.

Pure calculators — nothing here mocks a request, because nothing here can make
one. The module does not import the sources package.
"""

import datetime

import pytest

from calculators.common import DAYS, DELTA, MULTIPLIER, PROBABILITY
from calculators.category_02_platoon_splits import (
    ARM_ANGLE_BUCKET_BOUNDS,
    ARM_SLOT_OVER_THE_TOP,
    ARM_SLOT_SIDEARM,
    ARM_SLOT_THREE_QUARTER,
    arm_slot_bucket,
    calc_09_hitter_season_vs_throws,
    calc_10_hitter_recent_vs_throws,
    calc_11_pitcher_season_vs_bats,
    calc_12_pitcher_recent_vs_bats,
    calc_13_switch_hitter_split_acuity,
    calc_14_arm_slot_match,
    calc_15_reverse_platoon_index,
    compute_category_02,
)

TODAY = datetime.date(2026, 8, 8)


def _line(pa=0, ab=0, h=0, so=0, bb=0):
    return {
        "plateAppearances": pa,
        "atBats": ab,
        "hits": h,
        "strikeOuts": so,
        "baseOnBalls": bb,
    }


# Freeman's real 2026 splits, verified against the live API 2026-08-08.
FREEMAN = {
    "vl": _line(pa=157, ab=140, h=37, so=29, bb=14),
    "vr": _line(pa=328, ab=287, h=94, so=49, bb=37),
}

LEAGUE = {
    "L_vs_L": {"rate": 0.2348, "denominator": 11672},
    "L_vs_R": {"rate": 0.2516, "denominator": 36850},
    "R_vs_L": {"rate": 0.2492, "denominator": 18523},
    "R_vs_R": {"rate": 0.2396, "denominator": 38113},
}


def _pitch(
    day=1,
    game_pk=1,
    at_bat=1,
    events=None,
    stand="L",
    p_throws="R",
    arm_angle=37.0,
    xba=None,
):
    return {
        "game_date": f"2026-08-{day:02d}",
        "game_pk": game_pk,
        "at_bat_number": at_bat,
        "events": events,
        "description": "hit_into_play" if events else "ball",
        "stand": stand,
        "p_throws": p_throws,
        "arm_angle": arm_angle,
        "estimated_ba_using_speedangle": xba,
        "estimated_woba_using_speedangle": None,
    }


# ---------------------------------------------------------------------------
# CALC_09 / CALC_11 — season splits
# ---------------------------------------------------------------------------


def test_calc_09_selects_the_split_for_the_starters_hand():
    """A RHP starter reads the vr line; a LHP starter reads vl."""
    vs_r = calc_09_hitter_season_vs_throws(FREEMAN, "R")
    vs_l = calc_09_hitter_season_vs_throws(FREEMAN, "L")

    assert isinstance(vs_r, PROBABILITY)
    assert vs_r.value.rate == pytest.approx(94 / 328)
    assert vs_r.value.denominator == 328
    assert vs_l.value.rate == pytest.approx(37 / 157)


def test_calc_09_is_per_plate_appearance_not_per_at_bat():
    """H/PA, not H/AB — p_hit is defined per PA in the model's foundation.

    Using batting average here would overstate the rate by roughly ten percent,
    since walks leave the AB denominator but not the PA one.
    """
    value = calc_09_hitter_season_vs_throws(FREEMAN, "R").value
    assert value.rate == pytest.approx(94 / 328)
    assert value.rate != pytest.approx(94 / 287)  # not batting average


@pytest.mark.parametrize("hand", [None, "S", "", "X"])
def test_calc_09_unknown_hand_returns_none(hand):
    assert calc_09_hitter_season_vs_throws(FREEMAN, hand) is None


def test_calc_09_absent_split_returns_none():
    assert calc_09_hitter_season_vs_throws({"vl": _line(pa=10, h=3)}, "R") is None


def test_calc_09_zero_pa_split_returns_none():
    """An empty sample is absence of evidence, not a 0.0 rate."""
    assert calc_09_hitter_season_vs_throws({"vr": _line()}, "R") is None


def test_calc_11_reads_the_batter_side():
    """A pitcher's vl line describes what left-handed batters did to him."""
    splits = {"vl": _line(pa=261, ab=236, h=49), "vr": _line(pa=180, ab=163, h=31)}
    assert calc_11_pitcher_season_vs_bats(splits, "L").value.rate == pytest.approx(
        49 / 261
    )
    assert calc_11_pitcher_season_vs_bats(splits, "R").value.rate == pytest.approx(
        31 / 180
    )


def test_calc_11_survives_a_batters_faced_sourced_line():
    """Regression: the pitching group has no plateAppearances key.

    The source layer maps battersFaced onto plateAppearances. If that ever
    regresses, the denominator is 0 and this returns None for every pitcher on
    the slate — silently, with nothing raising anywhere. Values are Wheeler's
    real 2026 vs-LHB line.
    """
    normalized = {"vl": _line(pa=261, ab=236, h=49, so=78, bb=22)}
    result = calc_11_pitcher_season_vs_bats(normalized, "L")

    assert result is not None
    assert result.value.denominator == 261


def test_calc_11_unnormalized_line_would_return_none():
    """Documents exactly what the missing normalization looks like downstream."""
    unnormalized = {"vl": {"atBats": 236, "hits": 49, "battersFaced": 261}}
    assert calc_11_pitcher_season_vs_bats(unnormalized, "L") is None


# ---------------------------------------------------------------------------
# CALC_10 / CALC_12 — windowed, from Statcast
# ---------------------------------------------------------------------------


def test_calc_10_counts_plate_appearances_not_pitches():
    """Six pitches across two PAs is a denominator of 2, not 6."""
    pitches = [
        _pitch(day=5, at_bat=1),
        _pitch(day=5, at_bat=1),
        _pitch(day=5, at_bat=1, events="single"),
        _pitch(day=5, at_bat=2),
        _pitch(day=5, at_bat=2),
        _pitch(day=5, at_bat=2, events="strikeout"),
    ]
    result = calc_10_hitter_recent_vs_throws(pitches, "R", today=TODAY)

    assert result["CALC_10"].value.denominator == 2
    assert result["CALC_10"].value.rate == pytest.approx(0.5)


def test_calc_10_filters_to_the_starters_hand():
    """Pitches from the other hand are not in the population."""
    pitches = [
        _pitch(day=5, at_bat=1, p_throws="R", events="single"),
        _pitch(day=5, game_pk=2, at_bat=1, p_throws="L", events="single"),
    ]
    result = calc_10_hitter_recent_vs_throws(pitches, "R", today=TODAY)
    assert result["CALC_10"].value.denominator == 1


def test_calc_10_window_excludes_today_and_anything_older():
    """DAYS(14) covers the 14 days ending yesterday; today has not been played."""
    pitches = [
        _pitch(day=8, at_bat=1, events="single"),  # today — excluded
        _pitch(day=7, game_pk=2, at_bat=1, events="single"),  # yesterday — in
    ]
    result = calc_10_hitter_recent_vs_throws(pitches, "R", today=TODAY)
    assert result["CALC_10"].value.denominator == 1


def test_calc_10_empty_window_returns_none_keys():
    """No records in range yields None, not a zero rate — keys still present."""
    old = [_pitch(day=1, at_bat=1, events="single")]
    result = calc_10_hitter_recent_vs_throws(old, "R", window=DAYS(3), today=TODAY)
    assert result == {"CALC_10": None, "CALC_10_XBA": None}


def test_calc_10_xba_has_its_own_denominator():
    """BA is over PAs, xBA over batted balls — never one shared denominator.

    Two PAs, only one of which put a ball in play with a reading.
    """
    pitches = [
        _pitch(day=5, at_bat=1, events="single", xba=0.8),
        _pitch(day=5, at_bat=2, events="strikeout"),
    ]
    result = calc_10_hitter_recent_vs_throws(pitches, "R", today=TODAY)

    assert result["CALC_10"].value.denominator == 2
    assert result["CALC_10_XBA"].value.denominator == 1
    assert result["CALC_10_XBA"].value.rate == pytest.approx(0.8)


@pytest.mark.parametrize("hand", [None, "S", "X"])
def test_calc_10_unknown_hand_returns_none_keys(hand):
    result = calc_10_hitter_recent_vs_throws([_pitch()], hand, today=TODAY)
    assert result == {"CALC_10": None, "CALC_10_XBA": None}


def test_calc_12_filters_on_batter_side():
    """The pitcher-side calculator selects on `stand`, not `p_throws`."""
    pitches = [
        _pitch(day=5, at_bat=1, stand="L", events="single"),
        _pitch(day=5, game_pk=2, at_bat=1, stand="R", events="single"),
        _pitch(day=5, game_pk=3, at_bat=1, stand="R", events="field_out"),
    ]
    result = calc_12_pitcher_recent_vs_bats(pitches, "R", today=TODAY)

    assert result["CALC_12"].value.denominator == 2
    assert result["CALC_12"].value.rate == pytest.approx(0.5)


def test_calc_12_emits_both_keys_when_empty():
    assert calc_12_pitcher_recent_vs_bats([], "L", today=TODAY) == {
        "CALC_12": None,
        "CALC_12_XBA": None,
    }


# ---------------------------------------------------------------------------
# CALC_13 — switch-hitter split acuity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["L", "R", None])
def test_calc_13_returns_none_for_non_switch_hitters(side):
    """A player with one side has no other side to differ from.

    Returning 0.0 would claim the two were equal, which is a different claim.
    """
    assert calc_13_switch_hitter_split_acuity(FREEMAN, side, "R") is None


def test_calc_13_positive_when_todays_side_is_stronger():
    result = calc_13_switch_hitter_split_acuity(FREEMAN, "S", "R")
    assert isinstance(result, DELTA)
    assert result.value.rate == pytest.approx(94 / 328 - 37 / 157)
    assert result.value.rate > 0


def test_calc_13_negative_when_todays_side_is_weaker():
    """Facing a LHP forces the weaker side here, so the delta goes negative."""
    result = calc_13_switch_hitter_split_acuity(FREEMAN, "S", "L")
    assert result.value.rate == pytest.approx(37 / 157 - 94 / 328)
    assert result.value.rate < 0


def test_calc_13_is_not_clamped_to_a_probability_range():
    """DELTA exists so a consumer cannot mistake a signed difference for p."""
    result = calc_13_switch_hitter_split_acuity(FREEMAN, "S", "L")
    assert result.value.rate < 0
    assert not isinstance(result, PROBABILITY)


def test_calc_13_denominator_is_the_limiting_side():
    """A differential is only as trustworthy as its thinner half.

    For switch hitters that half is structurally thin — the off-side sample is
    the one a platoon-savvy manager spends all season avoiding.
    """
    thin = {"vl": _line(pa=9, ab=8, h=4), "vr": _line(pa=400, ab=360, h=90)}
    result = calc_13_switch_hitter_split_acuity(thin, "S", "R")
    assert result.value.denominator == 9


def test_calc_13_missing_off_side_sample_returns_none():
    assert (
        calc_13_switch_hitter_split_acuity({"vr": _line(pa=300, h=80)}, "S", "R")
        is None
    )


# ---------------------------------------------------------------------------
# CALC_14 — arm slot
# ---------------------------------------------------------------------------


def test_arm_angle_bounds_are_the_derived_constants():
    assert ARM_ANGLE_BUCKET_BOUNDS == (30.0, 42.0)


@pytest.mark.parametrize(
    "angle,expected",
    [
        (-61.3, ARM_SLOT_SIDEARM),  # real submarine minimum
        (0.0, ARM_SLOT_SIDEARM),
        (29.9, ARM_SLOT_SIDEARM),
        (30.0, ARM_SLOT_THREE_QUARTER),  # lower bound is inclusive
        (37.0, ARM_SLOT_THREE_QUARTER),  # league median
        (41.9, ARM_SLOT_THREE_QUARTER),
        (42.0, ARM_SLOT_OVER_THE_TOP),  # upper bound is inclusive
        (68.7, ARM_SLOT_OVER_THE_TOP),  # real observed maximum
        (None, None),
    ],
)
def test_arm_slot_bucket_boundaries(angle, expected):
    assert arm_slot_bucket(angle) == expected


def test_calc_14_matches_the_starters_bucket():
    """Only the hitter's pitches from the starter's slot count."""
    pitches = [
        _pitch(at_bat=1, arm_angle=37.0, events="single"),  # three_quarter
        _pitch(game_pk=2, at_bat=1, arm_angle=38.0, events="field_out"),
        _pitch(game_pk=3, at_bat=1, arm_angle=10.0, events="single"),  # sidearm
    ]
    result = calc_14_arm_slot_match(pitches, starter_arm_angle=40.0)

    assert result.value.denominator == 2
    assert result.value.rate == pytest.approx(0.5)


def test_calc_14_submarine_starter_matches_submarine_history():
    pitches = [_pitch(at_bat=1, arm_angle=-45.0, events="single")]
    assert calc_14_arm_slot_match(pitches, starter_arm_angle=-61.3) is not None


def test_calc_14_empty_bucket_returns_none():
    """No history against that slot is None, not zero."""
    pitches = [_pitch(at_bat=1, arm_angle=10.0, events="single")]
    assert calc_14_arm_slot_match(pitches, starter_arm_angle=60.0) is None


def test_calc_14_excludes_pitches_with_no_arm_angle():
    """arm_angle only exists from 2024; a missing reading is not a slot."""
    pitches = [
        _pitch(at_bat=1, arm_angle=37.0, events="single"),
        _pitch(game_pk=2, at_bat=1, arm_angle=None, events="field_out"),
    ]
    assert calc_14_arm_slot_match(pitches, 37.0).value.denominator == 1


def test_calc_14_unknown_starter_angle_returns_none():
    assert calc_14_arm_slot_match([_pitch()], None) is None


# ---------------------------------------------------------------------------
# CALC_15 — reverse platoon index
# ---------------------------------------------------------------------------


def _hitter(vs_l_rate, vs_r_rate, pa=200):
    """Build splits with exact rates on both sides."""
    return {
        "vl": _line(pa=pa, ab=pa, h=round(vs_l_rate * pa)),
        "vr": _line(pa=pa, ab=pa, h=round(vs_r_rate * pa)),
    }


def test_calc_15_league_typical_hitter_scores_one():
    """A hitter whose gap matches the league's scores exactly 1.0."""
    league_gap = LEAGUE["L_vs_R"]["rate"] - LEAGUE["L_vs_L"]["rate"]
    splits = _hitter(vs_l_rate=0.240, vs_r_rate=0.240 + league_gap, pa=1000)
    result = calc_15_reverse_platoon_index(splits, "L", "R", LEAGUE)

    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(1.0, abs=1e-3)


def test_calc_15_conventional_and_reverse_land_on_opposite_sides_of_one():
    """The property the index exists to express."""
    conventional = _hitter(vs_l_rate=0.200, vs_r_rate=0.300)  # big platoon gap
    reverse = _hitter(vs_l_rate=0.300, vs_r_rate=0.200)  # same size, inverted

    facing_rhp_conv = calc_15_reverse_platoon_index(conventional, "L", "R", LEAGUE)
    facing_rhp_rev = calc_15_reverse_platoon_index(reverse, "L", "R", LEAGUE)

    assert facing_rhp_conv.value.rate > 1.0
    assert facing_rhp_rev.value.rate < 1.0


def test_calc_15_is_continuous_with_no_threshold_cliff():
    """A mild reverse split lands nearer 1.0 than an extreme one."""
    mild = _hitter(vs_l_rate=0.260, vs_r_rate=0.250)
    extreme = _hitter(vs_l_rate=0.320, vs_r_rate=0.190)

    mild_index = calc_15_reverse_platoon_index(mild, "L", "R", LEAGUE).value.rate
    extreme_index = calc_15_reverse_platoon_index(extreme, "L", "R", LEAGUE).value.rate

    assert extreme_index < mild_index < 1.0


def test_calc_15_must_not_be_read_as_a_probability():
    """Role is MULTIPLIER, and the value legitimately exceeds any probability."""
    strong = _hitter(vs_l_rate=0.150, vs_r_rate=0.400)
    result = calc_15_reverse_platoon_index(strong, "L", "R", LEAGUE)
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate > 1.0


def test_calc_15_switch_hitter_returns_none():
    """No fixed hand means no league cell to compare against; CALC_13 covers them."""
    assert calc_15_reverse_platoon_index(FREEMAN, "S", "R", LEAGUE) is None


def test_calc_15_without_a_baseline_returns_none():
    """No baseline is no comparison — the honest answer is None."""
    assert calc_15_reverse_platoon_index(FREEMAN, "L", "R", {}) is None


def test_calc_15_incomplete_baseline_returns_none():
    partial = {"L_vs_R": {"rate": 0.25}}
    assert calc_15_reverse_platoon_index(FREEMAN, "L", "R", partial) is None


def test_calc_15_missing_hitter_side_returns_none():
    one_sided = {"vr": _line(pa=300, h=80)}
    assert calc_15_reverse_platoon_index(one_sided, "L", "R", LEAGUE) is None


# ---------------------------------------------------------------------------
# compute_category_02
# ---------------------------------------------------------------------------

CATEGORY_02_KEYS = {
    "CALC_09",
    "CALC_10",
    "CALC_10_XBA",
    "CALC_11",
    "CALC_12",
    "CALC_12_XBA",
    "CALC_13",
    "CALC_14",
    "CALC_15",
}


def test_compute_category_02_returns_every_key_with_full_data():
    result = compute_category_02(
        hitter_splits=FREEMAN,
        pitcher_splits={"vl": _line(pa=261, ab=236, h=49)},
        hitter_pitches=[_pitch(day=5, at_bat=1, events="single")],
        pitcher_pitches=[_pitch(day=5, at_bat=1, stand="L", events="single")],
        batter_side="L",
        pitcher_throws="R",
        starter_arm_angle=37.0,
        league_baseline=LEAGUE,
        today=TODAY,
    )
    assert set(result) == CATEGORY_02_KEYS
    assert result["CALC_09"] is not None
    assert result["CALC_11"] is not None


def test_compute_category_02_with_no_inputs_returns_all_keys_as_none():
    """Absent inputs resolve to None rather than omitting keys.

    Matches compute_category_01's contract so a consumer can rely on the key set
    without knowing what was fetched.
    """
    result = compute_category_02()
    assert set(result) == CATEGORY_02_KEYS
    assert all(value is None for value in result.values())


def test_compute_category_02_resolves_a_switch_hitters_effective_side():
    """A switch hitter bats left against a RHP, so CALC_11 must read the vl line.

    Passing "S" straight through would make CALC_11 None for every switch hitter,
    while CALC_13 needs the raw "S" to identify him at all — so the aggregate has
    to hold both facts at once.
    """
    pitcher = {"vl": _line(pa=261, ab=236, h=49), "vr": _line(pa=180, ab=163, h=31)}
    result = compute_category_02(
        hitter_splits=FREEMAN,
        pitcher_splits=pitcher,
        batter_side="S",
        pitcher_throws="R",
        league_baseline=LEAGUE,
        today=TODAY,
    )

    assert result["CALC_11"].value.denominator == 261  # the vl line
    assert result["CALC_13"] is not None  # still identified as a switch hitter
    assert result["CALC_15"] is None  # no fixed hand for the baseline


def test_compute_category_02_non_switch_hitter_has_no_calc_13():
    result = compute_category_02(
        hitter_splits=FREEMAN,
        batter_side="L",
        pitcher_throws="R",
        league_baseline=LEAGUE,
        today=TODAY,
    )
    assert result["CALC_13"] is None
    assert result["CALC_09"] is not None
