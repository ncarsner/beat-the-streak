"""Tests for CALC_42-CALC_46 in calculators/category_06_lineup_game_context.py.

`CALC_41` is covered by `tests/test_category_06_lineup_game_context.py`, which
predates the rest of the category.
"""

import pytest

from calculators.category_06_lineup_game_context import (
    DELTA,
    EXPONENT,
    MAX_STARTER_FIRST_INNING,
    MULTIPLIER,
    PROBABILITY,
    TTO_BUCKETS,
    batter_tto,
    calc_42_preceding_batter_obp,
    calc_43_succeeding_batter_protection,
    calc_44_times_through_order,
    calc_45_bottom_ninth_pa_risk,
    calc_46_expected_scoring,
    compute_category_06,
    neighbour_spots,
    pitcher_tto,
)
from tests.conftest import _lineup_line, _start_pas, _tto_pa

CONTEXT = {
    "skipped_ninth": {"rate": 0.4429, "denominator": 1761, "skipped": 780},
    "pa_lost_per_skipped_ninth": 0.5,
}


def _full_lineup(overrides=None):
    """A nine-deep order of identical hitters, with named spots replaced.

    *overrides* is positional and keyed by spot because batting-order spots are
    integers, and Python will not accept an integer as a keyword argument.
    """
    lineup = {spot: _lineup_line() for spot in range(1, 10)}
    lineup.update(overrides or {})
    return lineup


# ---------------------------------------------------------------------------
# Lineup neighbours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spot,expected",
    [(1, (9, 2)), (2, (1, 3)), (5, (4, 6)), (8, (7, 9)), (9, (8, 1))],
)
def test_the_batting_order_wraps_at_both_ends(spot, expected):
    """The hitter before the leadoff man is the ninth hitter, and the hitter on
    deck behind the ninth is the leadoff man. Treating either as absent would
    blank CALC_42 for every leadoff hitter and CALC_43 for every ninth."""
    assert neighbour_spots(spot) == expected


@pytest.mark.parametrize("spot", [0, 10, -1, None])
def test_a_spot_outside_the_order_has_no_neighbours(spot):
    assert neighbour_spots(spot) is None


# ---------------------------------------------------------------------------
# CALC_42
# ---------------------------------------------------------------------------


def test_calc_42_reads_the_preceding_hitters_on_base_percentage():
    lineup = _full_lineup({2: _lineup_line(obp=".410", pa=500)})
    result = calc_42_preceding_batter_obp(lineup, 3)
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(0.410)


def test_calc_42_carries_the_predecessors_plate_appearances_as_the_sample():
    """The reading rests on the *other* hitter's season, not this one's."""
    lineup = _full_lineup({4: _lineup_line(obp=".390", pa=612)})
    assert calc_42_preceding_batter_obp(lineup, 5).value.denominator == 612


def test_calc_42_wraps_for_the_leadoff_hitter():
    """The leadoff man's predecessor is the ninth hitter, not nobody."""
    lineup = _full_lineup({9: _lineup_line(obp=".295", pa=300)})
    assert calc_42_preceding_batter_obp(lineup, 1).value.rate == pytest.approx(0.295)


def test_calc_42_is_none_when_the_predecessor_has_no_line():
    assert calc_42_preceding_batter_obp({3: _lineup_line()}, 3) is None


@pytest.mark.parametrize("lineup", [None, {}])
def test_calc_42_is_none_without_a_lineup(lineup):
    assert calc_42_preceding_batter_obp(lineup, 4) is None


def test_calc_42_is_none_for_a_hitter_with_no_plate_appearances():
    """A callup with an empty line reports no rate, not a zero one. The API
    serves ".---" in that case, which is also not a number."""
    lineup = _full_lineup({2: _lineup_line(obp=".---", pa=0)})
    assert calc_42_preceding_batter_obp(lineup, 3) is None


def test_calc_42_is_none_for_an_unparseable_rate():
    lineup = _full_lineup({2: _lineup_line(obp=".---", pa=12)})
    assert calc_42_preceding_batter_obp(lineup, 3) is None


# ---------------------------------------------------------------------------
# CALC_43
# ---------------------------------------------------------------------------


def test_calc_43_emits_both_ops_and_slugging():
    """The ROADMAP says "threat" without saying what it means. OPS is the
    conventional answer; slugging alone is arguably the better read, since a
    pitcher fears extra-base damage rather than walks."""
    lineup = _full_lineup({5: _lineup_line(ops=".940", slg=".560", pa=480)})
    result = calc_43_succeeding_batter_protection(lineup, 4)
    assert result["CALC_43"].value.rate == pytest.approx(0.940)
    assert result["CALC_43_SLG"].value.rate == pytest.approx(0.560)


def test_calc_43_reads_the_on_deck_hitter_not_this_one():
    lineup = _full_lineup({4: _lineup_line(ops=".600"), 6: _lineup_line(ops=".950")})
    assert calc_43_succeeding_batter_protection(lineup, 5)["CALC_43"].value.rate == (
        pytest.approx(0.950)
    )


def test_calc_43_wraps_for_the_ninth_hitter():
    lineup = _full_lineup({1: _lineup_line(ops=".910", pa=500)})
    assert calc_43_succeeding_batter_protection(lineup, 9)["CALC_43"].value.rate == (
        pytest.approx(0.910)
    )


@pytest.mark.parametrize("key", ["CALC_43", "CALC_43_SLG"])
def test_calc_43_keys_are_present_even_when_unresolvable(key):
    assert calc_43_succeeding_batter_protection(None, None)[key] is None


@pytest.mark.parametrize("role", [MULTIPLIER])
def test_calc_43_is_not_a_probability(role):
    """An OPS near .900 is not on the p_hit scale and must not blend into it."""
    lineup = _full_lineup()
    assert isinstance(calc_43_succeeding_batter_protection(lineup, 4)["CALC_43"], role)


# ---------------------------------------------------------------------------
# CALC_44 -- the two reconstructions
# ---------------------------------------------------------------------------


def test_the_pitcher_side_counts_repeat_encounters_with_each_batter():
    pas = _start_pas(turns=3, batters=(101, 102, 103))
    labelled = pitcher_tto(pas)
    assert [tto for tto, _ in labelled] == [1, 1, 1, 2, 2, 2, 3, 3, 3]


def test_the_pitcher_side_drops_relief_outings():
    """Every plate appearance of a relief outing lands in bucket 1, since a
    reliever rarely faces the same hitter twice. Keeping them drags bucket 1
    toward bullpen quality and inflates the apparent penalty."""
    start = _start_pas(game_pk=1, turns=2, batters=(101, 102))
    relief = [
        _tto_pa(game_pk=2, at_bat_number=n, inning=7, outs_when_up=1, batter=200 + n)
        for n in (1, 2, 3)
    ]
    assert len(pitcher_tto(start + relief)) == len(start)


def test_a_start_is_identified_by_inning_one_with_nobody_out():
    """Category 9's rule, reused. An outing beginning with one out is relief."""
    late = [
        _tto_pa(game_pk=3, at_bat_number=1, inning=1, outs_when_up=1, batter=101),
        _tto_pa(game_pk=3, at_bat_number=2, inning=1, outs_when_up=2, batter=102),
    ]
    assert pitcher_tto(late) == []


def test_the_batter_side_identifies_the_starter_from_his_own_first_pa():
    """Category 9's start test cannot be reused on a batter frame: it holds only
    this batter's plate appearances, so a pitcher's earliest row in it is the
    first time he faced *this hitter*, usually the second inning or later."""
    pas = [
        _tto_pa(game_pk=1, at_bat_number=1, inning=1, pitcher=500),
        _tto_pa(game_pk=1, at_bat_number=2, inning=4, pitcher=500),
        _tto_pa(game_pk=1, at_bat_number=3, inning=6, pitcher=500),
        _tto_pa(game_pk=1, at_bat_number=4, inning=8, pitcher=999),  # a reliever
    ]
    assert [tto for tto, _ in batter_tto(pas)] == [1, 2, 3]


def test_the_batter_side_drops_a_game_the_hitter_entered_late():
    """A pinch hitter debuting in the ninth would otherwise crown a reliever as
    the starter and report a bogus first time through the order."""
    pas = [
        _tto_pa(
            game_pk=1,
            at_bat_number=1,
            inning=MAX_STARTER_FIRST_INNING + 1,
            pitcher=777,
        )
    ]
    assert batter_tto(pas) == []


def test_the_batter_side_accepts_a_first_pa_within_the_guard():
    pas = [
        _tto_pa(game_pk=1, at_bat_number=1, inning=MAX_STARTER_FIRST_INNING, pitcher=1)
    ]
    assert len(batter_tto(pas)) == 1


def test_each_game_yields_exactly_one_first_time_through():
    """An internal consistency check that held on real data: the batter-side
    bucket-1 count equalled the game count exactly, 59 and 111."""
    pas = []
    for game in (1, 2, 3):
        pas += [
            _tto_pa(game_pk=game, at_bat_number=1, inning=1, pitcher=500),
            _tto_pa(game_pk=game, at_bat_number=2, inning=4, pitcher=500),
        ]
    firsts = [tto for tto, _ in batter_tto(pas) if tto == 1]
    assert len(firsts) == 3


@pytest.mark.parametrize("reconstruction", [pitcher_tto, batter_tto])
def test_spring_training_is_excluded_from_both_reconstructions(reconstruction):
    spring = _start_pas(game_pk=9, turns=1, batters=(101,), game_type="S")
    regular = _start_pas(game_pk=1, turns=1, batters=(101,))
    assert len(reconstruction(spring + regular)) == 1


@pytest.mark.parametrize("reconstruction", [pitcher_tto, batter_tto])
def test_both_reconstructions_reduce_to_the_terminal_pitch(reconstruction):
    """Times through the order is an attribute of a plate appearance, not a
    pitch, so a six-pitch at-bat must not count as six."""
    pas = [
        _tto_pa(game_pk=1, at_bat_number=1, pitch_number=n, inning=1, events=None)
        for n in (1, 2)
    ]
    pas.append(
        _tto_pa(game_pk=1, at_bat_number=1, pitch_number=3, inning=1, events="single")
    )
    assert len(reconstruction(pas)) == 1


# ---------------------------------------------------------------------------
# CALC_44 -- the calculator
# ---------------------------------------------------------------------------


def test_calc_44_reports_a_hit_rate_per_bucket_on_both_sides():
    pitcher = _start_pas(turns=3, batters=(101, 102), events="single")
    batter = [
        _tto_pa(game_pk=1, at_bat_number=1, inning=1, pitcher=5, events="field_out"),
        _tto_pa(game_pk=1, at_bat_number=2, inning=4, pitcher=5, events="double"),
    ]
    result = calc_44_times_through_order(pitcher, batter)
    assert result["CALC_44_PITCHER_TTO1"].value.rate == 1.0
    assert result["CALC_44_BATTER_TTO1"].value.rate == 0.0
    assert result["CALC_44_BATTER_TTO2"].value.rate == 1.0


def test_calc_44_hit_rates_are_probabilities_on_the_models_own_scale():
    result = calc_44_times_through_order(_start_pas(turns=1, batters=(101,)), [])
    assert isinstance(result["CALC_44_PITCHER_TTO1"], PROBABILITY)


def test_calc_44_penalty_is_the_third_pass_minus_the_first():
    """The probe starter ran .251 / .270 / .337, a +.085 penalty."""
    pas = _start_pas(turns=3, batters=(101, 102, 103, 104), events="field_out")
    # Make every third-time-through plate appearance a hit.
    third = [pa for tto, pa in pitcher_tto(pas) if tto == 3]
    for pa in third:
        pa["events"] = "single"
    result = calc_44_times_through_order(pas, [])
    assert isinstance(result["CALC_44_PENALTY"], DELTA)
    assert result["CALC_44_PENALTY"].value.rate == pytest.approx(1.0)


def test_calc_44_penalty_may_be_negative():
    """A starter who holds up is a real reading, not a missing one."""
    pas = _start_pas(turns=3, batters=(101, 102), events="single")
    for pa in [pa for tto, pa in pitcher_tto(pas) if tto == 3]:
        pa["events"] = "field_out"
    assert calc_44_times_through_order(pas, [])["CALC_44_PENALTY"].value.rate < 0


def test_calc_44_penalty_is_none_when_either_leg_is_missing():
    """A difference against a missing baseline is not a small number."""
    pas = _start_pas(turns=2, batters=(101,))
    assert calc_44_times_through_order(pas, [])["CALC_44_PENALTY"] is None


def test_calc_44_ignores_a_fourth_time_through():
    pas = _start_pas(turns=4, batters=(101,))
    result = calc_44_times_through_order(pas, [])
    assert set(TTO_BUCKETS) == {1, 2, 3}
    assert "CALC_44_PITCHER_TTO4" not in result


def test_calc_44_keys_are_present_with_no_input():
    result = calc_44_times_through_order()
    assert len(result) == 7
    assert all(value is None for value in result.values())


# ---------------------------------------------------------------------------
# CALC_45
# ---------------------------------------------------------------------------


def test_calc_45_is_a_negative_plate_appearance_adjustment_for_a_home_hitter():
    result = calc_45_bottom_ninth_pa_risk(True, CONTEXT)
    assert isinstance(result, EXPONENT)
    assert result.value.rate == pytest.approx(-0.4429 * 0.5)


def test_calc_45_is_exactly_zero_for_a_road_hitter():
    """The top of the ninth is always played, so a road hitter is genuinely
    unaffected. None would make "no risk" indistinguishable from "no data"."""
    result = calc_45_bottom_ninth_pa_risk(False, CONTEXT)
    assert result is not None
    assert result.value.rate == 0.0


def test_calc_45_refuses_to_guess_which_side_the_hitter_is_on():
    assert calc_45_bottom_ninth_pa_risk(None, CONTEXT) is None


def test_calc_45_carries_the_measured_game_count():
    assert calc_45_bottom_ninth_pa_risk(True, CONTEXT).value.denominator == 1761


@pytest.mark.parametrize(
    "context", [None, {}, {"skipped_ninth": {}}, {"pa_lost_per_skipped_ninth": 0.5}]
)
def test_calc_45_is_none_for_a_home_hitter_without_the_league_rate(context):
    """A missing table returns None rather than falling back to an assumed rate."""
    assert calc_45_bottom_ninth_pa_risk(True, context) is None


def test_calc_45_is_an_exponent_not_a_probability():
    """It adjusts PA_proj, the exponent in 1 - (1 - p_hit)^PA_proj."""
    assert isinstance(calc_45_bottom_ninth_pa_risk(False, CONTEXT), EXPONENT)


# ---------------------------------------------------------------------------
# CALC_46
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total,mean", [(None, None), (9.5, None), (None, 8.6), (9.5, 0)]
)
def test_calc_46_is_none_without_both_numbers(total, mean):
    """Neither is reachable today: no source publishes betting lines, and there
    is no league mean of a quantity that cannot be fetched."""
    assert calc_46_expected_scoring(total, mean) is None


def test_calc_46_computes_a_ratio_when_both_are_supplied():
    result = calc_46_expected_scoring(9.5, 8.6)
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(9.5 / 8.6)


def test_calc_46_has_no_default_league_mean():
    """An invented reference would make every game look like a measured
    deviation from a real league average."""
    assert calc_46_expected_scoring(9.5) is None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


EXPECTED_KEYS = {
    "CALC_41",
    "CALC_42",
    "CALC_43",
    "CALC_43_SLG",
    "CALC_44_PITCHER_TTO1",
    "CALC_44_PITCHER_TTO2",
    "CALC_44_PITCHER_TTO3",
    "CALC_44_BATTER_TTO1",
    "CALC_44_BATTER_TTO2",
    "CALC_44_BATTER_TTO3",
    "CALC_44_PENALTY",
    "CALC_45",
    "CALC_46",
}


def test_absent_input_resolves_every_key_to_none_rather_than_omitting_it():
    result = compute_category_06()
    assert set(result) == EXPECTED_KEYS
    assert all(value is None for value in result.values())


def test_the_key_set_does_not_depend_on_what_was_fetched():
    result = compute_category_06(
        lineup_spot=3,
        lineup=_full_lineup(),
        pitcher_pitches=_start_pas(turns=3, batters=(101, 102)),
        batter_pitches=[_tto_pa(game_pk=1, at_bat_number=1, inning=1, pitcher=5)],
        batter_is_home=True,
        game_context=CONTEXT,
    )
    assert set(result) == EXPECTED_KEYS
    assert result["CALC_41"] is not None
    assert result["CALC_42"] is not None
    assert result["CALC_44_PITCHER_TTO3"] is not None
    assert result["CALC_45"] is not None
    assert result["CALC_46"] is None


def test_calc_41_still_behaves_as_it_did_before_the_category_was_completed():
    """The rest of Category 6 landing must not disturb the one calculator that
    was already shipped and is already depended on."""
    assert compute_category_06(lineup_spot=1)["CALC_41"].value.rate == pytest.approx(
        4.60
    )
