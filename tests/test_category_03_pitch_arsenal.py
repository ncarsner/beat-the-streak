"""Tests for calculators/category_03_pitch_arsenal.py (CALC_16-CALC_23)."""

import pytest

from calculators.common import DELTA, MULTIPLIER, PROBABILITY, terminal_pitch_by_pa
from calculators.category_03_pitch_arsenal import (
    EXTENSION_TIER_AVERAGE,
    EXTENSION_TIER_LONG,
    EXTENSION_TIER_SHORT,
    EXTREME_PFX_X_FEET,
    PITCH_CLASS_BREAKING,
    PITCH_CLASS_FASTBALL,
    PITCH_CLASS_OFFSPEED,
    VAA_TIER_AVERAGE,
    VAA_TIER_FLAT,
    VAA_TIER_STEEP,
    VELOCITY_TIER_92_TO_96,
    VELOCITY_TIER_OVER_96,
    VELOCITY_TIER_UNDER_92,
    _hit_rate,
    _mean_xba,
    _usage_share,
    calc_16_primary_fastball_xba_match,
    calc_17_breaking_ball_xba_match,
    calc_18_offspeed_xba_match,
    calc_19_run_value_match,
    calc_20_velocity_tier_match,
    calc_21_vaa_match,
    calc_22_horizontal_break_acuity,
    calc_23_extension_match,
    compute_category_03,
    extension_tier,
    fastball_vaa_tier,
    pitch_class,
    top_pitch_types,
    velocity_tier,
    vertical_approach_angle,
)
from tests.conftest import REAL_CURVEBALL, REAL_FOUR_SEAMER, _arsenal_pitch


# ---------------------------------------------------------------------------
# Pitch classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("FF", PITCH_CLASS_FASTBALL),
        ("SI", PITCH_CLASS_FASTBALL),
        ("FC", PITCH_CLASS_FASTBALL),
        ("SL", PITCH_CLASS_BREAKING),
        ("ST", PITCH_CLASS_BREAKING),
        ("CU", PITCH_CLASS_BREAKING),
        ("KC", PITCH_CLASS_BREAKING),
        ("SV", PITCH_CLASS_BREAKING),
        ("CS", PITCH_CLASS_BREAKING),
        ("CH", PITCH_CLASS_OFFSPEED),
        ("FS", PITCH_CLASS_OFFSPEED),
        ("FO", PITCH_CLASS_OFFSPEED),
    ],
)
def test_pitch_class_maps_every_grouped_code(code, expected):
    assert pitch_class(code) == expected


@pytest.mark.parametrize("code", [None, "PO", "IN", "EP", "KN", "UN", "FA", ""])
def test_ungrouped_codes_are_unclassified_not_defaulted(code):
    """Pitchouts and knuckleballs belong to no class and must not be assigned one."""
    assert pitch_class(code) is None


def test_cutter_is_a_fastball():
    """The arguable call, asserted explicitly so a change to it is deliberate."""
    assert pitch_class("FC") == PITCH_CLASS_FASTBALL


# ---------------------------------------------------------------------------
# Velocity tiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mph,expected",
    [
        (85.0, VELOCITY_TIER_UNDER_92),
        (91.9, VELOCITY_TIER_UNDER_92),
        (92.0, VELOCITY_TIER_92_TO_96),
        (94.5, VELOCITY_TIER_92_TO_96),
        (96.0, VELOCITY_TIER_92_TO_96),
        (96.1, VELOCITY_TIER_OVER_96),
        (101.0, VELOCITY_TIER_OVER_96),
    ],
)
def test_velocity_tier_boundaries_are_inclusive_of_the_named_range(mph, expected):
    """The ROADMAP's bracket is "92-96 mph", so both endpoints belong to it."""
    assert velocity_tier(mph) == expected


def test_missing_velocity_is_no_tier():
    assert velocity_tier(None) is None


# ---------------------------------------------------------------------------
# Vertical approach angle
# ---------------------------------------------------------------------------


def test_vaa_matches_the_hand_solved_value_for_a_real_four_seamer():
    assert vertical_approach_angle(dict(REAL_FOUR_SEAMER)) == pytest.approx(
        -6.086148, abs=1e-5
    )


def test_vaa_matches_the_hand_solved_value_for_a_real_curveball():
    assert vertical_approach_angle(dict(REAL_CURVEBALL)) == pytest.approx(
        -11.443100, abs=1e-5
    )


def test_curveball_descends_more_steeply_than_a_fastball():
    """The physical ordering that validated the formula's sign.

    An inverted sign, or a dropped `ay` term, still returns plausible-looking
    degrees — this ordering is what actually catches either.
    """
    fastball = vertical_approach_angle(dict(REAL_FOUR_SEAMER))
    curveball = vertical_approach_angle(dict(REAL_CURVEBALL))
    assert curveball < fastball < 0


@pytest.mark.parametrize("missing", ["vy0", "ay", "vz0", "az"])
def test_any_missing_trajectory_term_yields_no_reading(missing):
    pitch = dict(REAL_FOUR_SEAMER)
    pitch[missing] = None
    assert vertical_approach_angle(pitch) is None


def test_absent_trajectory_keys_yield_no_reading():
    assert vertical_approach_angle({"pitch_type": "FF"}) is None


def test_zero_ay_does_not_divide_by_zero():
    pitch = dict(REAL_FOUR_SEAMER, ay=0.0)
    assert vertical_approach_angle(pitch) is None


def test_unphysical_trajectory_returns_none_rather_than_raising():
    """A negative discriminant has no real square root; that is not an exception."""
    assert (
        vertical_approach_angle({"vy0": 1.0, "ay": 50.0, "vz0": 0.0, "az": 0.0}) is None
    )


# ---------------------------------------------------------------------------
# VAA and extension tiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vaa,expected",
    [
        (-9.0, VAA_TIER_STEEP),
        (-5.70, VAA_TIER_STEEP),
        (-5.69, VAA_TIER_AVERAGE),
        (-5.00, VAA_TIER_AVERAGE),
        (-4.69, VAA_TIER_FLAT),
        (-3.00, VAA_TIER_FLAT),
    ],
)
def test_flat_means_least_negative(vaa, expected):
    """Reading the boundaries as ordinary ascending numbers inverts the label."""
    assert fastball_vaa_tier(vaa) == expected


def test_missing_vaa_is_no_tier():
    assert fastball_vaa_tier(None) is None


@pytest.mark.parametrize(
    "feet,expected",
    [
        (5.5, EXTENSION_TIER_SHORT),
        (6.29, EXTENSION_TIER_SHORT),
        (6.30, EXTENSION_TIER_AVERAGE),
        (6.60, EXTENSION_TIER_AVERAGE),
        (6.61, EXTENSION_TIER_LONG),
        (7.2, EXTENSION_TIER_LONG),
    ],
)
def test_extension_tier_boundaries(feet, expected):
    assert extension_tier(feet) == expected


def test_missing_extension_is_no_tier():
    assert extension_tier(None) is None


# ---------------------------------------------------------------------------
# Terminal-pitch reduction
# ---------------------------------------------------------------------------


def test_terminal_pitch_is_the_highest_pitch_number_in_the_pa():
    pitches = [
        _arsenal_pitch(pitch_number=1, pitch_type="FF", release_speed=97.0),
        _arsenal_pitch(
            pitch_number=3, pitch_type="SL", release_speed=84.0, events="single"
        ),
        _arsenal_pitch(pitch_number=2, pitch_type="FF", release_speed=96.0),
    ]
    terminal = terminal_pitch_by_pa(pitches)
    assert len(terminal) == 1
    assert terminal[0]["pitch_number"] == 3
    assert terminal[0]["pitch_type"] == "SL"


def test_input_order_does_not_decide_the_terminal_pitch():
    """Statcast row order is not a contract; the reduction must not depend on it."""
    pitches = [
        _arsenal_pitch(pitch_number=4, events="strikeout"),
        _arsenal_pitch(pitch_number=1),
    ]
    assert terminal_pitch_by_pa(pitches)[0]["events"] == "strikeout"
    assert terminal_pitch_by_pa(list(reversed(pitches)))[0]["events"] == "strikeout"


def test_plate_appearances_are_keyed_on_game_and_at_bat_together():
    """The same at_bat_number in two games is two plate appearances, not one."""
    pitches = [
        _arsenal_pitch(game_pk=1, at_bat_number=7, pitch_number=1, events="single"),
        _arsenal_pitch(game_pk=2, at_bat_number=7, pitch_number=1, events="single"),
    ]
    assert len(terminal_pitch_by_pa(pitches)) == 2


@pytest.mark.parametrize("field", ["game_pk", "at_bat_number", "pitch_number"])
def test_rows_missing_an_identifying_field_are_dropped(field):
    """Dropped, not defaulted — a None key would merge unrelated PAs into one."""
    pitch = _arsenal_pitch(events="single")
    pitch[field] = None
    assert terminal_pitch_by_pa([pitch]) == []


def test_no_pitches_yields_no_plate_appearances():
    assert terminal_pitch_by_pa([]) == []


# ---------------------------------------------------------------------------
# Rate helpers
# ---------------------------------------------------------------------------


def test_hit_rate_counts_every_hit_event():
    terminal = [
        _arsenal_pitch(at_bat_number=1, events="single"),
        _arsenal_pitch(at_bat_number=2, events="double"),
        _arsenal_pitch(at_bat_number=3, events="triple"),
        _arsenal_pitch(at_bat_number=4, events="home_run"),
        _arsenal_pitch(at_bat_number=5, events="field_out"),
        _arsenal_pitch(at_bat_number=6, events="walk"),
    ]
    rate = _hit_rate(terminal)
    assert rate.rate == pytest.approx(4 / 6)
    assert rate.denominator == 6


def test_walks_stay_in_the_denominator():
    """Rates are per plate appearance, not per at-bat; a walk is a PA."""
    assert _hit_rate(
        [
            _arsenal_pitch(at_bat_number=1, events="single"),
            _arsenal_pitch(at_bat_number=2, events="walk"),
        ]
    ).rate == pytest.approx(0.5)


def test_empty_sample_is_none_not_zero():
    assert _hit_rate([]) is None


def test_mean_xba_denominator_counts_batted_balls_not_plate_appearances():
    """A pitch with no xBA reading was not put in play and must not dilute the mean."""
    pitches = [
        _arsenal_pitch(xba=0.400),
        _arsenal_pitch(xba=0.200),
        _arsenal_pitch(xba=None),
        _arsenal_pitch(xba=None),
    ]
    xba = _mean_xba(pitches)
    assert xba.rate == pytest.approx(0.300)
    assert xba.denominator == 2


def test_mean_xba_is_none_when_nothing_was_put_in_play():
    assert _mean_xba([_arsenal_pitch(xba=None)]) is None


def test_usage_share_denominator_excludes_untyped_pitches():
    pitches = [
        _arsenal_pitch(pitch_type="FF"),
        _arsenal_pitch(pitch_type="SL"),
        _arsenal_pitch(pitch_type=None),
    ]
    usage = _usage_share(pitches, lambda p: p["pitch_type"] == "FF")
    assert usage.rate == pytest.approx(0.5)
    assert usage.denominator == 2


def test_the_three_class_usages_sum_to_under_one_when_a_pitch_is_unclassified():
    """The residue stays visible instead of being renormalized away."""
    pitcher = [
        _arsenal_pitch(pitch_type="FF"),
        _arsenal_pitch(pitch_type="SL"),
        _arsenal_pitch(pitch_type="CH"),
        _arsenal_pitch(pitch_type="PO"),  # pitchout: classified as nothing
    ]
    total = sum(
        _usage_share(pitcher, lambda p, c=cls: pitch_class(p["pitch_type"]) == c).rate
        for cls in (PITCH_CLASS_FASTBALL, PITCH_CLASS_BREAKING, PITCH_CLASS_OFFSPEED)
    )
    assert total == pytest.approx(0.75)


def test_usage_share_is_none_when_nothing_carries_a_pitch_type():
    assert _usage_share([_arsenal_pitch(pitch_type=None)], lambda p: True) is None


# ---------------------------------------------------------------------------
# CALC_16 / CALC_17 / CALC_18
# ---------------------------------------------------------------------------


def _three_class_pitcher():
    return (
        [_arsenal_pitch(pitch_type="FF") for _ in range(6)]
        + [_arsenal_pitch(pitch_type="SL") for _ in range(3)]
        + [_arsenal_pitch(pitch_type="CH")]
    )


@pytest.mark.parametrize(
    "calculator,key,code,usage",
    [
        (calc_16_primary_fastball_xba_match, "CALC_16", "SI", 0.6),
        (calc_17_breaking_ball_xba_match, "CALC_17", "CU", 0.3),
        (calc_18_offspeed_xba_match, "CALC_18", "FS", 0.1),
    ],
)
def test_class_calculators_pair_hitter_xba_with_starter_usage(
    calculator, key, code, usage
):
    """The hitter's code need not match the starter's; the *class* is what matches."""
    hitter = [_arsenal_pitch(pitch_type=code, xba=0.320)]
    result = calculator(hitter, _three_class_pitcher())
    assert result[key].value.rate == pytest.approx(0.320)
    assert result[f"{key}_USAGE"].value.rate == pytest.approx(usage)


def test_class_xba_reads_only_pitches_of_that_class():
    hitter = [
        _arsenal_pitch(pitch_type="FF", xba=0.500),
        _arsenal_pitch(pitch_type="SL", xba=0.100),
    ]
    result = calc_16_primary_fastball_xba_match(hitter, _three_class_pitcher())
    assert result["CALC_16"].value.rate == pytest.approx(0.500)
    assert result["CALC_16"].value.denominator == 1


def test_class_calculator_roles_are_probability_and_multiplier():
    """A usage share is not a hit probability and the type must say so."""
    result = calc_16_primary_fastball_xba_match(
        [_arsenal_pitch(pitch_type="FF", xba=0.3)], _three_class_pitcher()
    )
    assert isinstance(result["CALC_16"], PROBABILITY)
    assert isinstance(result["CALC_16_USAGE"], MULTIPLIER)


def test_a_starter_who_throws_no_offspeed_has_a_zero_share_not_a_missing_one():
    """Zero usage is a measurement; None would mean the arsenal was unknown."""
    result = calc_18_offspeed_xba_match([], [_arsenal_pitch(pitch_type="FF")])
    assert result["CALC_18_USAGE"].value.rate == 0.0
    assert result["CALC_18"] is None


def test_both_keys_are_none_when_neither_side_has_data():
    result = calc_17_breaking_ball_xba_match([], [])
    assert result == {"CALC_17": None, "CALC_17_USAGE": None}


# ---------------------------------------------------------------------------
# CALC_19
# ---------------------------------------------------------------------------


def test_top_pitch_types_returns_the_two_most_thrown():
    pitcher = (
        [_arsenal_pitch(pitch_type="FF") for _ in range(5)]
        + [_arsenal_pitch(pitch_type="SL") for _ in range(3)]
        + [_arsenal_pitch(pitch_type="CH")]
    )
    assert top_pitch_types(pitcher) == ["FF", "SL"]


def test_top_pitch_types_breaks_ties_alphabetically_not_by_input_order():
    """Without a deterministic tiebreak the output depends on Statcast row order."""
    forward = [_arsenal_pitch(pitch_type="SL"), _arsenal_pitch(pitch_type="CH")]
    assert (
        top_pitch_types(forward, n=1)
        == top_pitch_types(list(reversed(forward)), n=1)
        == ["CH"]
    )


def test_top_pitch_types_is_empty_for_an_untyped_arsenal():
    assert top_pitch_types([_arsenal_pitch(pitch_type=None)]) == []


def test_calc_19_scales_run_value_to_one_hundred_pitches():
    pitcher = [_arsenal_pitch(pitch_type="FF")]
    hitter = [
        _arsenal_pitch(pitch_type="FF", delta_run_exp=0.10),
        _arsenal_pitch(pitch_type="FF", delta_run_exp=-0.02),
    ]
    result = calc_19_run_value_match(hitter, pitcher)
    assert result.value.rate == pytest.approx(4.0)  # 100 * 0.08 / 2
    assert result.value.denominator == 2


def test_calc_19_is_positive_when_the_hitter_gains_run_expectancy():
    """`delta_run_exp` is from the batting team's perspective: a home run is +1.6.

    Reaching for `delta_pitcher_run_exp` instead would invert every sign and
    still return numbers of a plausible magnitude.
    """
    pitcher = [_arsenal_pitch(pitch_type="FF")]
    homer = calc_19_run_value_match(
        [_arsenal_pitch(pitch_type="FF", delta_run_exp=1.633, events="home_run")],
        pitcher,
    )
    whiff = calc_19_run_value_match(
        [_arsenal_pitch(pitch_type="FF", delta_run_exp=-0.229, events="strikeout")],
        pitcher,
    )
    assert homer.value.rate > 0 > whiff.value.rate


def test_calc_19_ignores_pitches_outside_the_starters_top_types():
    pitcher = [_arsenal_pitch(pitch_type="FF")]
    hitter = [
        _arsenal_pitch(pitch_type="FF", delta_run_exp=0.10),
        _arsenal_pitch(pitch_type="CU", delta_run_exp=-9.99),
    ]
    assert calc_19_run_value_match(hitter, pitcher).value.denominator == 1


def test_calc_19_role_is_delta_because_run_value_is_not_a_probability():
    result = calc_19_run_value_match(
        [_arsenal_pitch(pitch_type="FF", delta_run_exp=0.5)],
        [_arsenal_pitch(pitch_type="FF")],
    )
    assert isinstance(result, DELTA)
    assert not isinstance(result, PROBABILITY)


@pytest.mark.parametrize(
    "hitter,pitcher",
    [
        ([], [_arsenal_pitch(pitch_type="FF")]),
        ([_arsenal_pitch(pitch_type="FF", delta_run_exp=0.1)], []),
    ],
)
def test_calc_19_needs_both_sides(hitter, pitcher):
    assert calc_19_run_value_match(hitter, pitcher) is None


# ---------------------------------------------------------------------------
# CALC_20
# ---------------------------------------------------------------------------


def test_calc_20_grades_the_starter_on_fastballs_only():
    """A deep breaking-ball mix must not make a power arm look soft.

    The starter below averages 88.5 mph across his whole arsenal — the under-92
    bracket — but throws 98 when he throws a fastball.
    """
    pitcher = [_arsenal_pitch(pitch_type="FF", release_speed=98.0)] + [
        _arsenal_pitch(pitch_type="CU", release_speed=79.0) for _ in range(3)
    ]
    hitter = [
        _arsenal_pitch(at_bat_number=1, release_speed=98.0, events="single"),
        _arsenal_pitch(at_bat_number=2, release_speed=98.0, events="field_out"),
        _arsenal_pitch(at_bat_number=3, release_speed=85.0, events="single"),
        _arsenal_pitch(at_bat_number=4, release_speed=85.0, events="single"),
    ]
    result = calc_20_velocity_tier_match(hitter, pitcher)
    assert result.value.rate == pytest.approx(0.5)  # the two >96 PAs only
    assert result.value.denominator == 2


def test_calc_20_rate_is_taken_over_every_pitch_type_in_the_bracket():
    """The bracket is defined on speed alone, so a hard slider counts."""
    pitcher = [_arsenal_pitch(pitch_type="FF", release_speed=93.0)]
    hitter = [
        _arsenal_pitch(
            at_bat_number=1, pitch_type="FF", release_speed=93.0, events="single"
        ),
        _arsenal_pitch(
            at_bat_number=2, pitch_type="SL", release_speed=93.0, events="single"
        ),
    ]
    assert calc_20_velocity_tier_match(hitter, pitcher).value.denominator == 2


def test_calc_20_uses_the_terminal_pitch_of_each_plate_appearance():
    """A PA that saw 97 but ended on an 84 mph slider is not an over-96 PA."""
    pitcher = [_arsenal_pitch(pitch_type="FF", release_speed=98.0)]
    hitter = [
        _arsenal_pitch(pitch_number=1, release_speed=98.0),
        _arsenal_pitch(pitch_number=2, release_speed=84.0, events="strikeout"),
    ]
    assert calc_20_velocity_tier_match(hitter, pitcher) is None


def test_calc_20_is_none_when_the_starter_threw_no_fastballs():
    pitcher = [_arsenal_pitch(pitch_type="CU", release_speed=78.0)]
    assert (
        calc_20_velocity_tier_match([_arsenal_pitch(events="single")], pitcher) is None
    )


def test_calc_20_is_none_when_the_hitter_never_saw_that_bracket():
    pitcher = [_arsenal_pitch(pitch_type="FF", release_speed=99.0)]
    hitter = [_arsenal_pitch(release_speed=85.0, events="single")]
    assert calc_20_velocity_tier_match(hitter, pitcher) is None


# ---------------------------------------------------------------------------
# CALC_21
# ---------------------------------------------------------------------------


def test_calc_21_matches_the_hitter_to_the_starters_fastball_vaa_tier():
    pitcher = [_arsenal_pitch(pitch_type="FF", trajectory=REAL_FOUR_SEAMER)]
    hitter = [
        _arsenal_pitch(
            at_bat_number=1,
            pitch_type="FF",
            trajectory=REAL_FOUR_SEAMER,
            events="single",
        ),
        _arsenal_pitch(
            at_bat_number=2,
            pitch_type="FF",
            trajectory=REAL_FOUR_SEAMER,
            events="field_out",
        ),
    ]
    result = calc_21_vaa_match(hitter, pitcher)
    assert (
        fastball_vaa_tier(vertical_approach_angle(dict(REAL_FOUR_SEAMER)))
        == VAA_TIER_STEEP
    )
    assert result["CALC_21"].value.rate == pytest.approx(0.5)
    assert isinstance(result["CALC_21"], PROBABILITY)


def test_calc_21_ignores_the_hitters_non_fastballs():
    """Pooled across pitch types, VAA measures arsenal mix rather than delivery."""
    pitcher = [_arsenal_pitch(pitch_type="FF", trajectory=REAL_FOUR_SEAMER)]
    hitter = [
        _arsenal_pitch(
            at_bat_number=1,
            pitch_type="FF",
            trajectory=REAL_FOUR_SEAMER,
            events="single",
        ),
        _arsenal_pitch(
            at_bat_number=2,
            pitch_type="CU",
            trajectory=REAL_FOUR_SEAMER,
            events="field_out",
        ),
    ]
    assert calc_21_vaa_match(hitter, pitcher)["CALC_21"].value.denominator == 1


def test_calc_21_is_none_when_the_starter_has_no_fastball_trajectory():
    pitcher = [_arsenal_pitch(pitch_type="FF")]  # no trajectory keys
    assert calc_21_vaa_match([], pitcher)["CALC_21"] is None


def test_plane_mismatch_is_zero_when_the_swing_parallels_the_pitch():
    """An attack angle of +6.086 exactly offsets a VAA of -6.086."""
    hitter = [_arsenal_pitch(trajectory=REAL_FOUR_SEAMER, attack_angle=6.086148)]
    result = calc_21_vaa_match(hitter, [])
    assert result["CALC_21_PLANE_MISMATCH"].value.rate == pytest.approx(0.0, abs=1e-5)


def test_plane_mismatch_is_positive_for_a_swing_steeper_than_the_pitch():
    steep = calc_21_vaa_match(
        [_arsenal_pitch(trajectory=REAL_FOUR_SEAMER, attack_angle=20.0)], []
    )
    flat = calc_21_vaa_match(
        [_arsenal_pitch(trajectory=REAL_FOUR_SEAMER, attack_angle=-5.0)], []
    )
    assert steep["CALC_21_PLANE_MISMATCH"].value.rate > 0
    assert flat["CALC_21_PLANE_MISMATCH"].value.rate < 0


def test_plane_mismatch_denominator_counts_swings_only():
    """`attack_angle` exists only where the batter swung, and only from 2024."""
    hitter = [
        _arsenal_pitch(trajectory=REAL_FOUR_SEAMER, attack_angle=10.0),
        _arsenal_pitch(trajectory=REAL_FOUR_SEAMER, attack_angle=None),
    ]
    assert (
        calc_21_vaa_match(hitter, [])["CALC_21_PLANE_MISMATCH"].value.denominator == 1
    )


def test_plane_mismatch_is_none_for_a_pre_bat_tracking_season():
    """No `attack_angle` column at all is the pre-2024 shape, and it must not crash."""
    hitter = [{"game_pk": 1, "at_bat_number": 1, "pitch_number": 1, **REAL_FOUR_SEAMER}]
    assert calc_21_vaa_match(hitter, [])["CALC_21_PLANE_MISMATCH"] is None


def test_plane_mismatch_role_is_delta_because_degrees_are_not_a_rate():
    result = calc_21_vaa_match(
        [_arsenal_pitch(trajectory=REAL_FOUR_SEAMER, attack_angle=10.0)], []
    )
    assert isinstance(result["CALC_21_PLANE_MISMATCH"], DELTA)


def test_calc_21_emits_both_keys_even_with_no_data():
    assert calc_21_vaa_match([], []) == {
        "CALC_21": None,
        "CALC_21_PLANE_MISMATCH": None,
    }


# ---------------------------------------------------------------------------
# CALC_22
# ---------------------------------------------------------------------------


def test_extreme_break_counts_both_directions():
    """A sweeper and a sinker break opposite ways; magnitude is what is extreme."""
    pitcher = [
        _arsenal_pitch(pfx_x=EXTREME_PFX_X_FEET),
        _arsenal_pitch(pfx_x=-EXTREME_PFX_X_FEET),
        _arsenal_pitch(pfx_x=0.2),
    ]
    result = calc_22_horizontal_break_acuity([], pitcher)
    assert result["CALC_22_USAGE"].value.rate == pytest.approx(2 / 3)


def test_extreme_break_threshold_is_inclusive():
    just_under = calc_22_horizontal_break_acuity(
        [], [_arsenal_pitch(pfx_x=EXTREME_PFX_X_FEET - 0.01)]
    )
    at_bound = calc_22_horizontal_break_acuity(
        [], [_arsenal_pitch(pfx_x=EXTREME_PFX_X_FEET)]
    )
    assert just_under["CALC_22_USAGE"].value.rate == 0.0
    assert at_bound["CALC_22_USAGE"].value.rate == 1.0


def test_calc_22_hit_rate_covers_only_pas_ended_by_an_extreme_breaking_pitch():
    hitter = [
        _arsenal_pitch(at_bat_number=1, pfx_x=1.4, events="single"),
        _arsenal_pitch(at_bat_number=2, pfx_x=-1.5, events="field_out"),
        _arsenal_pitch(at_bat_number=3, pfx_x=0.1, events="single"),
    ]
    result = calc_22_horizontal_break_acuity(hitter, [_arsenal_pitch(pfx_x=1.4)])
    assert result["CALC_22"].value.rate == pytest.approx(0.5)
    assert result["CALC_22"].value.denominator == 2


def test_calc_22_usage_denominator_counts_pitches_with_a_break_reading():
    pitcher = [_arsenal_pitch(pfx_x=1.4), _arsenal_pitch(pfx_x=None)]
    assert (
        calc_22_horizontal_break_acuity([], pitcher)["CALC_22_USAGE"].value.denominator
        == 1
    )


def test_calc_22_emits_both_keys_even_with_no_data():
    assert calc_22_horizontal_break_acuity([], []) == {
        "CALC_22": None,
        "CALC_22_USAGE": None,
    }


# ---------------------------------------------------------------------------
# CALC_23
# ---------------------------------------------------------------------------


def test_calc_23_matches_the_hitter_to_the_starters_extension_tier():
    pitcher = [_arsenal_pitch(release_extension=7.0)]
    hitter = [
        _arsenal_pitch(at_bat_number=1, release_extension=7.1, events="single"),
        _arsenal_pitch(at_bat_number=2, release_extension=6.9, events="field_out"),
        _arsenal_pitch(at_bat_number=3, release_extension=5.8, events="single"),
    ]
    result = calc_23_extension_match(hitter, pitcher)
    assert result["CALC_23"].value.rate == pytest.approx(0.5)  # the two long-tier PAs
    assert isinstance(result["CALC_23"], PROBABILITY)


def test_calc_23_extension_is_averaged_across_the_whole_arsenal():
    """Extension is a property of the delivery, not of the fastball.

    Fastballs alone would put this starter at 7.0 feet — the long tier. Across
    the whole arsenal he averages 6.3, which is the average tier, and that is
    the tier the hitter's plate appearance below belongs to.
    """
    pitcher = [
        _arsenal_pitch(pitch_type="FF", release_extension=7.0),
        _arsenal_pitch(pitch_type="CU", release_extension=5.6),
    ]
    hitter = [_arsenal_pitch(release_extension=6.4, events="single")]
    assert extension_tier(7.0) == EXTENSION_TIER_LONG
    assert calc_23_extension_match(hitter, pitcher)["CALC_23"].value.rate == 1.0


def test_velo_gain_is_the_perceived_minus_actual_speed():
    pitcher = [
        _arsenal_pitch(release_speed=94.0, effective_speed=95.0),
        _arsenal_pitch(release_speed=94.0, effective_speed=94.4),
    ]
    gain = calc_23_extension_match([], pitcher)["CALC_23_VELO_GAIN"]
    assert gain.value.rate == pytest.approx(0.7)
    assert gain.value.denominator == 2
    assert isinstance(gain, DELTA)


def test_velo_gain_is_negative_for_a_short_release():
    pitcher = [_arsenal_pitch(release_speed=94.0, effective_speed=93.2)]
    assert calc_23_extension_match([], pitcher)["CALC_23_VELO_GAIN"].value.rate < 0


def test_velo_gain_needs_both_speed_readings():
    pitcher = [_arsenal_pitch(release_speed=94.0, effective_speed=None)]
    assert calc_23_extension_match([], pitcher)["CALC_23_VELO_GAIN"] is None


def test_calc_23_emits_both_keys_even_with_no_data():
    assert calc_23_extension_match([], []) == {
        "CALC_23": None,
        "CALC_23_VELO_GAIN": None,
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


EXPECTED_KEYS = {
    "CALC_16",
    "CALC_16_USAGE",
    "CALC_17",
    "CALC_17_USAGE",
    "CALC_18",
    "CALC_18_USAGE",
    "CALC_19",
    "CALC_20",
    "CALC_21",
    "CALC_21_PLANE_MISMATCH",
    "CALC_22",
    "CALC_22_USAGE",
    "CALC_23",
    "CALC_23_VELO_GAIN",
}


def test_compute_emits_every_key_with_no_inputs_at_all():
    """Absent inputs resolve to None rather than omitting keys, as in Categories 1-2."""
    result = compute_category_03()
    assert set(result) == EXPECTED_KEYS
    assert all(value is None for value in result.values())


def test_compute_key_set_is_stable_when_data_is_present():
    pitcher = [
        _arsenal_pitch(
            pitch_type="FF",
            release_speed=95.0,
            effective_speed=95.4,
            release_extension=6.5,
            pfx_x=1.3,
            trajectory=REAL_FOUR_SEAMER,
        )
    ]
    hitter = [
        _arsenal_pitch(
            at_bat_number=1,
            pitch_type="FF",
            release_speed=95.0,
            release_extension=6.5,
            pfx_x=1.3,
            delta_run_exp=0.2,
            xba=0.35,
            attack_angle=8.0,
            trajectory=REAL_FOUR_SEAMER,
            events="single",
        )
    ]
    assert set(compute_category_03(hitter, pitcher)) == EXPECTED_KEYS


def test_compute_accepts_positional_hitter_then_pitcher_order():
    """The hitter's rows come first, matching every calculator's signature."""
    hitter = [_arsenal_pitch(pitch_type="FF", xba=0.400)]
    pitcher = [_arsenal_pitch(pitch_type="SL")]
    result = compute_category_03(hitter, pitcher)
    assert result["CALC_16"].value.rate == pytest.approx(0.400)
    assert result["CALC_16_USAGE"].value.rate == 0.0
