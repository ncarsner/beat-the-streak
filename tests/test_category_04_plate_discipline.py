"""Tests for the Category 4 plate-discipline calculators."""

import pytest

from calculators.common import MULTIPLIER, PROBABILITY
from calculators.category_04_plate_discipline import (
    CONTACT_DESCRIPTIONS,
    IN_ZONE_CODES,
    OUT_OF_ZONE_CODES,
    SWING_DESCRIPTIONS,
    UNTHROWN_DESCRIPTIONS,
    WHIFF_DESCRIPTIONS,
    calc_24_zone_contact_match,
    calc_25_chase_vulnerability,
    calc_26_whiff_overlay,
    calc_27_csw_interaction,
    calc_28_first_pitch_attack,
    calc_29_two_strike_protection,
    calc_30_quadrant_acuity,
    compute_category_04,
    hitter_zone_xba,
    is_contact,
    is_in_zone,
    is_out_of_zone,
    is_swing,
    is_whiff,
    pitcher_zone_weights,
    was_thrown,
    zone_code,
)
from tests.conftest import _discipline_pitch


# ---------------------------------------------------------------------------
# Description vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description,swing",
    [
        ("swinging_strike", True),
        ("swinging_strike_blocked", True),
        ("missed_bunt", True),
        ("foul", True),
        ("foul_tip", True),
        ("foul_bunt", True),
        ("hit_into_play", True),
        ("ball", False),
        ("blocked_ball", False),
        ("called_strike", False),
        ("hit_by_pitch", False),
        ("automatic_ball", False),
        (None, False),
    ],
)
def test_is_swing_covers_the_observed_vocabulary(description, swing):
    assert is_swing(_discipline_pitch(description=description)) is swing


@pytest.mark.parametrize(
    "description,whiff",
    [
        ("swinging_strike", True),
        ("swinging_strike_blocked", True),
        ("missed_bunt", True),
        ("foul", False),
        ("hit_into_play", False),
        ("called_strike", False),
    ],
)
def test_is_whiff_only_counts_swings_that_touched_nothing(description, whiff):
    assert is_whiff(_discipline_pitch(description=description)) is whiff


def test_a_foul_tip_is_contact_not_a_whiff():
    """The bat touched the ball, which is why a caught foul tip is a strikeout by
    rule rather than a foul ball. Savant counts it as contact and so do we."""
    pitch = _discipline_pitch(description="foul_tip")
    assert is_contact(pitch) is True
    assert is_whiff(pitch) is False
    assert is_swing(pitch) is True


def test_swings_partition_cleanly_into_contact_and_whiffs():
    assert WHIFF_DESCRIPTIONS | CONTACT_DESCRIPTIONS == SWING_DESCRIPTIONS
    assert not (WHIFF_DESCRIPTIONS & CONTACT_DESCRIPTIONS)


@pytest.mark.parametrize("description", sorted(UNTHROWN_DESCRIPTIONS))
def test_automatic_count_events_are_not_thrown_pitches(description):
    assert was_thrown(_discipline_pitch(description=description)) is False


def test_an_ordinary_ball_counts_as_thrown():
    assert was_thrown(_discipline_pitch(description="ball")) is True


# ---------------------------------------------------------------------------
# Zone coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [(5, 5), (5.0, 5), (14.0, 14), (11, 11)])
def test_zone_code_coerces_floats_to_ints(raw, expected):
    """pandas widens an int column to float64 to hold nulls, and the cache's CSV
    round-trip preserves that, so a zone arrives as 5.0 rather than 5."""
    assert zone_code(_discipline_pitch(zone=raw)) == expected


@pytest.mark.parametrize("raw", [None, "", "abc", float("nan"), True, False])
def test_zone_code_returns_none_for_anything_unusable(raw):
    assert zone_code(_discipline_pitch(zone=raw)) is None


def test_a_float_zone_still_matches_the_int_code_sets():
    """The regression this guards: comparing 5.0 against a set of ints matches
    nothing and silently empties every zone-based rate in the category."""
    assert is_in_zone(_discipline_pitch(zone=5.0)) is True
    assert is_out_of_zone(_discipline_pitch(zone=13.0)) is True


@pytest.mark.parametrize("code", sorted(IN_ZONE_CODES))
def test_codes_one_through_nine_are_in_the_zone(code):
    assert is_in_zone(_discipline_pitch(zone=code)) is True
    assert is_out_of_zone(_discipline_pitch(zone=code)) is False


@pytest.mark.parametrize("code", sorted(OUT_OF_ZONE_CODES))
def test_codes_eleven_through_fourteen_are_out_of_the_zone(code):
    assert is_out_of_zone(_discipline_pitch(zone=code)) is True
    assert is_in_zone(_discipline_pitch(zone=code)) is False


def test_there_is_no_zone_ten():
    assert 10 not in IN_ZONE_CODES
    assert 10 not in OUT_OF_ZONE_CODES
    assert is_in_zone(_discipline_pitch(zone=10)) is False
    assert is_out_of_zone(_discipline_pitch(zone=10)) is False


def test_an_untracked_pitch_is_in_neither_zone_set():
    pitch = _discipline_pitch(zone=None)
    assert is_in_zone(pitch) is False
    assert is_out_of_zone(pitch) is False


# ---------------------------------------------------------------------------
# CALC_24
# ---------------------------------------------------------------------------


def test_calc_24_zone_contact_is_contact_per_in_zone_swing():
    hitter = [
        _discipline_pitch(zone=5, description="foul"),
        _discipline_pitch(zone=5, description="hit_into_play"),
        _discipline_pitch(zone=5, description="hit_into_play"),
        _discipline_pitch(zone=5, description="swinging_strike"),
    ]
    result = calc_24_zone_contact_match(hitter, [])
    assert isinstance(result["CALC_24"], MULTIPLIER)
    assert result["CALC_24"].value.rate == pytest.approx(0.75)
    assert result["CALC_24"].value.denominator == 4


def test_calc_24_excludes_takes_and_out_of_zone_swings():
    """A called strike is not a swing, and an out-of-zone whiff is CALC_25's
    problem, not this calculator's."""
    hitter = [
        _discipline_pitch(zone=5, description="hit_into_play"),
        _discipline_pitch(zone=5, description="called_strike"),
        _discipline_pitch(zone=5, description="ball"),
        _discipline_pitch(zone=13, description="swinging_strike"),
    ]
    result = calc_24_zone_contact_match(hitter, [])
    assert result["CALC_24"].value.denominator == 1
    assert result["CALC_24"].value.rate == pytest.approx(1.0)


def test_calc_24_zone_rate_is_over_tracked_pitches_only():
    """An untracked pitch shrinks the sample rather than counting as a non-strike
    and understating the rate."""
    pitcher = [
        _discipline_pitch(zone=5),
        _discipline_pitch(zone=13),
        _discipline_pitch(zone=None, description="automatic_ball"),
    ]
    result = calc_24_zone_contact_match([], pitcher)
    assert result["CALC_24_ZONE_RATE"].value.rate == pytest.approx(0.5)
    assert result["CALC_24_ZONE_RATE"].value.denominator == 2


def test_calc_24_returns_none_on_each_side_independently():
    result = calc_24_zone_contact_match([], [_discipline_pitch(zone=5)])
    assert result["CALC_24"] is None
    assert result["CALC_24_ZONE_RATE"] is not None


# ---------------------------------------------------------------------------
# CALC_25
# ---------------------------------------------------------------------------


def test_calc_25_chase_is_swings_per_out_of_zone_pitch_seen():
    """Denominator is out-of-zone pitches seen, not out-of-zone swings: a hitter
    who never chases has a rate, and it is zero."""
    hitter = [
        _discipline_pitch(zone=13, description="swinging_strike"),
        _discipline_pitch(zone=14, description="ball"),
        _discipline_pitch(zone=11, description="ball"),
        _discipline_pitch(zone=12, description="ball"),
        _discipline_pitch(zone=5, description="swinging_strike"),
    ]
    result = calc_25_chase_vulnerability(hitter, [])
    assert result["CALC_25"].value.rate == pytest.approx(0.25)
    assert result["CALC_25"].value.denominator == 4


def test_calc_24_and_calc_25_zone_rates_are_complements():
    pitcher = [_discipline_pitch(zone=z) for z in (1, 5, 9, 11, 12, 13, 14, 2, 3, 4)]
    zone_rate = calc_24_zone_contact_match([], pitcher)["CALC_24_ZONE_RATE"]
    ozone_rate = calc_25_chase_vulnerability([], pitcher)["CALC_25_OZONE_RATE"]
    assert zone_rate.value.rate + ozone_rate.value.rate == pytest.approx(1.0)
    assert zone_rate.value.denominator == ozone_rate.value.denominator


# ---------------------------------------------------------------------------
# CALC_26
# ---------------------------------------------------------------------------


def test_calc_26_uses_different_denominators_on_the_two_sides():
    """Hitter whiff is per swing; pitcher swinging-strike is per pitch. The same
    four pitches therefore produce different numbers on each side, which is the
    ROADMAP's definition and the industry convention, not a bug."""
    pitches = [
        _discipline_pitch(description="swinging_strike"),
        _discipline_pitch(description="foul"),
        _discipline_pitch(description="ball"),
        _discipline_pitch(description="called_strike"),
    ]
    result = calc_26_whiff_overlay(pitches, pitches)
    assert result["CALC_26"].value.rate == pytest.approx(0.5)
    assert result["CALC_26"].value.denominator == 2
    assert result["CALC_26_SWSTR"].value.rate == pytest.approx(0.25)
    assert result["CALC_26_SWSTR"].value.denominator == 4


def test_calc_26_pitcher_side_excludes_unthrown_pitches():
    pitcher = [
        _discipline_pitch(description="swinging_strike"),
        _discipline_pitch(description="ball"),
        _discipline_pitch(description="automatic_ball", zone=None),
    ]
    result = calc_26_whiff_overlay([], pitcher)
    assert result["CALC_26_SWSTR"].value.denominator == 2
    assert result["CALC_26_SWSTR"].value.rate == pytest.approx(0.5)


def test_calc_26_returns_none_for_a_hitter_who_never_swung():
    hitter = [_discipline_pitch(description="ball")]
    assert calc_26_whiff_overlay(hitter, [])["CALC_26"] is None


# ---------------------------------------------------------------------------
# CALC_27
# ---------------------------------------------------------------------------


def test_calc_27_pools_called_strikes_and_whiffs_per_pitch():
    pitches = [
        _discipline_pitch(description="called_strike"),
        _discipline_pitch(description="swinging_strike"),
        _discipline_pitch(description="foul"),
        _discipline_pitch(description="ball"),
    ]
    result = calc_27_csw_interaction(pitches, pitches)
    assert result["CALC_27"].value.rate == pytest.approx(0.5)
    assert result["CALC_27_PITCHER_CSW"].value.rate == pytest.approx(0.5)


def test_calc_27_does_not_double_count_a_pitch_that_is_both_tests():
    """No description satisfies both halves, but the numerator is an `or` and a
    future vocabulary change could make one. Counting it twice would let CSW
    exceed 1.0."""
    pitches = [_discipline_pitch(description="swinging_strike")] * 3
    result = calc_27_csw_interaction(pitches, [])
    assert result["CALC_27"].value.rate == pytest.approx(1.0)


def test_calc_27_excludes_unthrown_pitches_from_the_denominator():
    pitches = [
        _discipline_pitch(description="called_strike"),
        _discipline_pitch(description="automatic_ball", zone=None),
    ]
    result = calc_27_csw_interaction(pitches, [])
    assert result["CALC_27"].value.denominator == 1
    assert result["CALC_27"].value.rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CALC_28
# ---------------------------------------------------------------------------


def test_calc_28_counts_only_the_opening_pitch_of_each_plate_appearance():
    hitter = [
        _discipline_pitch(at_bat_number=1, pitch_number=1, description="foul"),
        _discipline_pitch(at_bat_number=1, pitch_number=2, description="foul"),
        _discipline_pitch(at_bat_number=2, pitch_number=1, description="ball"),
        _discipline_pitch(at_bat_number=3, pitch_number=1, description="ball"),
        _discipline_pitch(at_bat_number=3, pitch_number=2, description="foul"),
    ]
    result = calc_28_first_pitch_attack(hitter, [])
    assert result["CALC_28"].value.denominator == 3
    assert result["CALC_28"].value.rate == pytest.approx(1 / 3)


def test_calc_28_f_strike_counts_fouls_and_balls_in_play_as_strikes():
    """The standard F-Strike definition is "anything that is not a ball", which
    Statcast's `type` column gives directly as S or X."""
    pitcher = [
        _discipline_pitch(at_bat_number=n, pitch_number=1, description=d)
        for n, d in enumerate(
            ["called_strike", "foul", "hit_into_play", "swinging_strike", "ball"], 1
        )
    ]
    result = calc_28_first_pitch_attack([], pitcher)
    assert result["CALC_28_F_STRIKE"].value.rate == pytest.approx(0.8)
    assert result["CALC_28_F_STRIKE"].value.denominator == 5


def test_calc_28_treats_a_hit_batsman_as_a_non_strike():
    pitcher = [
        _discipline_pitch(at_bat_number=1, pitch_number=1, description="hit_by_pitch"),
        _discipline_pitch(at_bat_number=2, pitch_number=1, description="called_strike"),
    ]
    result = calc_28_first_pitch_attack([], pitcher)
    assert result["CALC_28_F_STRIKE"].value.rate == pytest.approx(0.5)


def test_calc_28_drops_automatic_balls_from_both_sides():
    """A pitch-timer violation on 0-0 puts the count at 1-0 without a delivery.
    Counting it would charge the hitter a non-swing he was never offered and the
    pitcher a non-strike he never threw."""
    pitches = [
        _discipline_pitch(at_bat_number=1, pitch_number=1, description="foul"),
        _discipline_pitch(
            at_bat_number=2, pitch_number=1, description="automatic_ball", zone=None
        ),
    ]
    result = calc_28_first_pitch_attack(pitches, pitches)
    assert result["CALC_28"].value.denominator == 1
    assert result["CALC_28"].value.rate == pytest.approx(1.0)
    assert result["CALC_28_F_STRIKE"].value.denominator == 1
    assert result["CALC_28_F_STRIKE"].value.rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CALC_29
# ---------------------------------------------------------------------------


def test_calc_29_hitter_side_is_contact_per_two_strike_swing():
    hitter = [
        _discipline_pitch(strikes=2, description="foul"),
        _discipline_pitch(strikes=2, description="swinging_strike"),
        _discipline_pitch(strikes=1, description="swinging_strike"),
        _discipline_pitch(strikes=2, description="called_strike"),
    ]
    result = calc_29_two_strike_protection(hitter, [])
    assert result["CALC_29"].value.rate == pytest.approx(0.5)
    assert result["CALC_29"].value.denominator == 2


def _pa(at_bat_number, terminal_strikes, events, length=3):
    """A plate appearance whose terminal pitch carries *terminal_strikes*."""
    pitches = [
        _discipline_pitch(
            at_bat_number=at_bat_number,
            pitch_number=n,
            strikes=min(n - 1, terminal_strikes),
            description="foul",
        )
        for n in range(1, length)
    ]
    pitches.append(
        _discipline_pitch(
            at_bat_number=at_bat_number,
            pitch_number=length,
            strikes=terminal_strikes,
            events=events,
            description="hit_into_play",
        )
    )
    return pitches


def test_calc_29_pitcher_side_is_outs_per_two_strike_plate_appearance():
    pitcher = (
        _pa(1, 2, "strikeout")
        + _pa(2, 2, "field_out")
        + _pa(3, 2, "single")
        + _pa(4, 1, "single")
    )
    result = calc_29_two_strike_protection([], pitcher)
    assert result["CALC_29_PITCHER_OUT"].value.denominator == 3
    assert result["CALC_29_PITCHER_OUT"].value.rate == pytest.approx(2 / 3)


def test_calc_29_reduces_to_the_terminal_pitch_before_filtering_on_the_count():
    """The CALC_14 defect in issue #37, reproduced in miniature. Every pitch of
    this plate appearance carries a different count, and only the last one is a
    two-strike pitch. Filtering pitch rows on `strikes == 2` and grouping second
    would be harmless here, but the mirror case is not: reduce first and the
    denominator is one plate appearance carrying its real outcome, never a set of
    rows whose `events` is None and which score as outs."""
    pitcher = [
        _discipline_pitch(at_bat_number=1, pitch_number=1, strikes=0, events=None),
        _discipline_pitch(at_bat_number=1, pitch_number=2, strikes=1, events=None),
        _discipline_pitch(at_bat_number=1, pitch_number=3, strikes=2, events="single"),
    ]
    result = calc_29_two_strike_protection([], pitcher)
    assert result["CALC_29_PITCHER_OUT"].value.denominator == 1
    assert result["CALC_29_PITCHER_OUT"].value.rate == pytest.approx(0.0)


def test_calc_29_excludes_a_plate_appearance_that_never_reached_two_strikes():
    pitcher = _pa(1, 1, "single")
    assert calc_29_two_strike_protection([], pitcher)["CALC_29_PITCHER_OUT"] is None


@pytest.mark.parametrize(
    "events", ["truncated_pa", "caught_stealing_2b", "pickoff_1b", "wild_pitch", None]
)
def test_calc_29_drops_events_that_do_not_resolve_the_batter(events):
    """Treating "not on base" as "out" would score a caught stealing as a pitcher
    success. An unrecognized event shrinks the sample instead."""
    pitcher = _pa(1, 2, "strikeout") + _pa(2, 2, events)
    result = calc_29_two_strike_protection([], pitcher)
    assert result["CALC_29_PITCHER_OUT"].value.denominator == 1
    assert result["CALC_29_PITCHER_OUT"].value.rate == pytest.approx(1.0)


@pytest.mark.parametrize(
    "events,is_out",
    [
        ("field_out", True),
        ("strikeout", True),
        ("grounded_into_double_play", True),
        ("sac_fly", True),
        ("fielders_choice_out", True),
        ("single", False),
        ("home_run", False),
        ("walk", False),
        ("hit_by_pitch", False),
        ("field_error", False),
        ("fielders_choice", False),
        ("catcher_interf", False),
    ],
)
def test_calc_29_classifies_terminal_events_correctly(events, is_out):
    """`fielders_choice` (batter reached) and `fielders_choice_out` (batter
    retired) differ by one word and land on opposite sides."""
    result = calc_29_two_strike_protection([], _pa(1, 2, events))
    assert result["CALC_29_PITCHER_OUT"].value.rate == pytest.approx(
        1.0 if is_out else 0.0
    )


# ---------------------------------------------------------------------------
# CALC_30
# ---------------------------------------------------------------------------


def test_hitter_zone_xba_averages_batted_balls_per_zone():
    hitter = [
        _discipline_pitch(zone=5, description="hit_into_play", xba=0.400),
        _discipline_pitch(zone=5, description="hit_into_play", xba=0.200),
        _discipline_pitch(zone=1, description="hit_into_play", xba=0.900),
        _discipline_pitch(zone=5, description="ball"),
    ]
    profile = hitter_zone_xba(hitter)
    assert profile[5].rate == pytest.approx(0.300)
    assert profile[5].denominator == 2
    assert profile[1].denominator == 1


def test_hitter_zone_xba_omits_zones_with_no_batted_ball():
    hitter = [_discipline_pitch(zone=5, description="hit_into_play", xba=0.5)]
    assert set(hitter_zone_xba(hitter)) == {5}


def test_hitter_zone_xba_ignores_out_of_zone_batted_balls():
    """CALC_30 is defined over the 3x3 in-zone grid. Chases are CALC_25's job."""
    hitter = [_discipline_pitch(zone=13, description="hit_into_play", xba=0.9)]
    assert hitter_zone_xba(hitter) == {}


def test_pitcher_zone_weights_are_renormalized_over_in_zone_pitches():
    """This is "where in the zone he works", not "how often he is in the zone".
    The latter is CALC_24_ZONE_RATE, and folding both into one number would make
    a wild starter look like a corner painter."""
    pitcher = [
        _discipline_pitch(zone=5),
        _discipline_pitch(zone=5),
        _discipline_pitch(zone=1),
        _discipline_pitch(zone=13),
        _discipline_pitch(zone=14),
    ]
    weights = pitcher_zone_weights(pitcher)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[5] == pytest.approx(2 / 3)
    assert weights[1] == pytest.approx(1 / 3)


def test_calc_30_weights_the_hitters_zone_profile_by_the_starters_locations():
    hitter = [
        _discipline_pitch(zone=1, description="hit_into_play", xba=0.100),
        _discipline_pitch(zone=5, description="hit_into_play", xba=0.500),
    ]
    pitcher = [_discipline_pitch(zone=1)] * 3 + [_discipline_pitch(zone=5)]
    result = calc_30_quadrant_acuity(hitter, pitcher)
    assert isinstance(result["CALC_30"], PROBABILITY)
    assert result["CALC_30"].value.rate == pytest.approx(0.75 * 0.1 + 0.25 * 0.5)
    assert result["CALC_30"].value.denominator == 2
    assert result["CALC_30_COVERAGE"].value.rate == pytest.approx(1.0)


def test_calc_30_renormalizes_over_covered_zones_only():
    """Without renormalization the weighted sum would be scaled down by however
    much of the heatmap the hitter has no sample for, and would read as a much
    worse hitter rather than as a thinner measurement."""
    hitter = [_discipline_pitch(zone=1, description="hit_into_play", xba=0.400)]
    pitcher = [_discipline_pitch(zone=1)] + [_discipline_pitch(zone=9)] * 3
    result = calc_30_quadrant_acuity(hitter, pitcher)
    assert result["CALC_30"].value.rate == pytest.approx(0.400)


def test_calc_30_coverage_is_weighted_by_the_starters_own_distribution():
    """Missing the zone he pounds is not the same problem as missing one he never
    touches, so coverage is a share of his pitches, not covered-zones over nine."""
    hitter = [_discipline_pitch(zone=1, description="hit_into_play", xba=0.4)]
    pitcher = [_discipline_pitch(zone=1)] + [_discipline_pitch(zone=9)] * 3
    result = calc_30_quadrant_acuity(hitter, pitcher)
    assert result["CALC_30_COVERAGE"].value.rate == pytest.approx(0.25)
    assert result["CALC_30_COVERAGE"].value.denominator == 2


def test_calc_30_reports_coverage_even_when_nothing_overlaps():
    """Zero overlap is a meaningful statement, not an absent one: the consumer
    learns the starter works exclusively where this hitter has no sample."""
    hitter = [_discipline_pitch(zone=1, description="hit_into_play", xba=0.4)]
    pitcher = [_discipline_pitch(zone=9)] * 3
    result = calc_30_quadrant_acuity(hitter, pitcher)
    assert result["CALC_30"] is None
    assert result["CALC_30_COVERAGE"].value.rate == pytest.approx(0.0)


def test_calc_30_returns_none_on_both_keys_without_a_hitter_sample():
    result = calc_30_quadrant_acuity([], [_discipline_pitch(zone=5)])
    assert result["CALC_30"] is None
    assert result["CALC_30_COVERAGE"] is None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


CATEGORY_04_KEYS = {
    "CALC_24",
    "CALC_24_ZONE_RATE",
    "CALC_25",
    "CALC_25_OZONE_RATE",
    "CALC_26",
    "CALC_26_SWSTR",
    "CALC_27",
    "CALC_27_PITCHER_CSW",
    "CALC_28",
    "CALC_28_F_STRIKE",
    "CALC_29",
    "CALC_29_PITCHER_OUT",
    "CALC_30",
    "CALC_30_COVERAGE",
}


@pytest.mark.parametrize(
    "hitter,pitcher",
    [(None, None), ([], []), ([_discipline_pitch()], None), (None, [])],
)
def test_compute_category_04_always_emits_the_full_key_set(hitter, pitcher):
    """Keys resolve to None rather than being omitted, so a consumer can rely on
    the key set without knowing what was fetched."""
    assert set(compute_category_04(hitter, pitcher)) == CATEGORY_04_KEYS


def test_compute_category_04_resolves_every_key_to_none_without_input():
    assert set(compute_category_04().values()) == {None}


def test_compute_category_04_populates_every_key_on_a_full_matchup():
    hitter = [
        _discipline_pitch(at_bat_number=1, pitch_number=1, zone=5, description="foul"),
        _discipline_pitch(
            at_bat_number=1,
            pitch_number=2,
            zone=5,
            strikes=2,
            description="hit_into_play",
            events="single",
            xba=0.6,
        ),
        _discipline_pitch(
            at_bat_number=2, pitch_number=1, zone=13, description="swinging_strike"
        ),
        _discipline_pitch(
            at_bat_number=2, pitch_number=2, zone=5, description="called_strike"
        ),
    ]
    pitcher = _pa(1, 2, "strikeout") + [
        _discipline_pitch(
            at_bat_number=5, pitch_number=1, zone=13, description="swinging_strike"
        )
    ]
    result = compute_category_04(hitter, pitcher)
    assert set(result) == CATEGORY_04_KEYS
    assert all(value is not None for value in result.values()), {
        k: v for k, v in result.items() if v is None
    }


def test_every_category_04_rate_is_a_role_tagged_value():
    """No calculator returns a bare Rate or a bare float: the sample size has to
    survive to whatever weights it later, and the role tag has to survive to tell
    a consumer these are not p_hit-scale."""
    hitter = [
        _discipline_pitch(
            zone=5, description="hit_into_play", strikes=2, events="single", xba=0.5
        )
    ]
    pitcher = _pa(1, 2, "field_out")
    for value in compute_category_04(hitter, pitcher).values():
        assert value is None or isinstance(value, (MULTIPLIER, PROBABILITY))


def test_calc_30_is_the_only_probability_in_the_category():
    """Everything else is a skill rate, not a batting average. Tagging a .85 zone
    contact rate as PROBABILITY would invite a consumer to blend it into p_hit."""
    hitter = [
        _discipline_pitch(
            zone=5, description="hit_into_play", strikes=2, events="single", xba=0.5
        )
    ]
    pitcher = _pa(1, 2, "field_out")
    result = compute_category_04(hitter, pitcher)
    probabilities = {k for k, v in result.items() if isinstance(v, PROBABILITY)}
    assert probabilities == {"CALC_30"}
