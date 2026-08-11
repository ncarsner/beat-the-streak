"""Tests for calculators/category_11_composite.py."""

from datetime import date

import pytest

from calculators.category_11_composite import (
    DEFAULT_HALF_LIFE_DAYS,
    TRENDING_WINDOW_DAYS,
    calc_72_season_base_rate,
    calc_73_trending_rate,
    calc_74_matchup_rate,
    calc_75_composite_hit_rate,
    calc_76_game_hit_probability,
    compute_category_11,
    projected_plate_appearances,
)
from calculators.common import EXPONENT, MULTIPLIER, PROBABILITY, Rate
from tests.conftest import _form_pa

TODAY = date(2026, 8, 9)


def _pas(*specs):
    """Pitch records for plate appearances given as ``(game_date, hit)`` pairs."""
    records = []
    for index, (game_date, hit) in enumerate(specs, start=1):
        records.append(
            _form_pa(
                events="single" if hit else "field_out",
                hit=hit,
                game_date=game_date,
                game_pk=hash(game_date) % 10000,
                at_bat_number=index,
            )
        )
    return records


def _prob(rate, denominator):
    return PROBABILITY(Rate(rate, denominator))


# ---------------------------------------------------------------------------
# CALC_72
# ---------------------------------------------------------------------------


def test_calc_72_is_hits_per_plate_appearance():
    pas = _pas(("2026-05-01", True), ("2026-05-01", False), ("2026-06-01", True))
    assert calc_72_season_base_rate(pas).value == (pytest.approx(2 / 3), 3)


def test_calc_72_drops_spring_training():
    """Inherited from Category 8's plate_appearances rather than re-implemented,
    which is the point of reusing it."""
    pas = _pas(("2026-08-01", True))
    spring = _form_pa(
        events="single", hit=True, game_date="2026-03-01", game_type="S", game_pk=99
    )
    assert calc_72_season_base_rate(pas + [spring]).value.denominator == 1


def test_calc_72_is_none_with_no_plate_appearances():
    assert calc_72_season_base_rate([]) is None


def test_calc_72_is_a_probability():
    assert isinstance(calc_72_season_base_rate(_pas(("2026-05-01", True))), PROBABILITY)


# ---------------------------------------------------------------------------
# CALC_73
# ---------------------------------------------------------------------------


def test_calc_73_weights_recent_plate_appearances_more():
    """A hit yesterday and an out two weeks ago must read above .500."""
    pas = _pas(("2026-08-08", True), ("2026-07-28", False))
    assert calc_73_trending_rate(pas, TODAY).value.rate > 0.5


def test_calc_73_weights_stale_plate_appearances_less():
    pas = _pas(("2026-07-28", True), ("2026-08-08", False))
    assert calc_73_trending_rate(pas, TODAY).value.rate < 0.5


def test_calc_73_denominator_is_the_sample_not_the_weight_sum():
    """Every Rate.denominator in this model is the sample behind that rate.
    The weight sum is right there and looks like one, which is the trap."""
    pas = _pas(("2026-08-08", True), ("2026-08-04", False), ("2026-07-30", True))
    assert calc_73_trending_rate(pas, TODAY).value.denominator == 3


def test_calc_73_converges_on_the_unweighted_rate_at_a_long_half_life():
    """CALC_54 is this window unweighted, so the two coincide in the limit,
    which is exactly why they must not both be blended."""
    pas = _pas(("2026-08-08", True), ("2026-07-28", False))
    assert calc_73_trending_rate(pas, TODAY, half_life_days=1e6).value.rate == (
        pytest.approx(0.5, abs=1e-5)
    )


def test_calc_73_ignores_plate_appearances_outside_the_window():
    pas = _pas(("2026-08-08", True), ("2026-06-01", False))
    assert calc_73_trending_rate(pas, TODAY).value.denominator == 1


def test_calc_73_window_matches_the_roadmap():
    assert TRENDING_WINDOW_DAYS == 14


def test_calc_73_is_none_for_an_empty_window():
    """A hitter who has not played in two weeks gets no answer, not a zero."""
    assert calc_73_trending_rate(_pas(("2026-06-01", True)), TODAY) is None


def test_calc_73_is_none_with_no_plate_appearances():
    assert calc_73_trending_rate([], TODAY) is None


def test_calc_73_rejects_a_non_positive_half_life():
    with pytest.raises(ValueError):
        calc_73_trending_rate(_pas(("2026-08-08", True)), TODAY, half_life_days=0)


def test_the_default_half_life_sits_inside_the_window():
    assert 0 < DEFAULT_HALF_LIFE_DAYS < TRENDING_WINDOW_DAYS


# ---------------------------------------------------------------------------
# CALC_74
# ---------------------------------------------------------------------------


def test_calc_74_weights_components_by_sample_size():
    """A 3-PA BvP history and a 400-PA platoon split are both evidence and are
    not equal evidence."""
    result = calc_74_matchup_rate(bvp=_prob(1.0, 3), platoon=_prob(0.25, 397))
    assert result.value == (pytest.approx((3 + 0.25 * 397) / 400), 400)


def test_calc_74_carries_the_total_sample():
    result = calc_74_matchup_rate(_prob(0.3, 10), _prob(0.25, 400), _prob(0.28, 90))
    assert result.value.denominator == 500


def test_calc_74_survives_a_missing_component():
    assert calc_74_matchup_rate(platoon=_prob(0.25, 400)).value == (0.25, 400)


def test_calc_74_is_none_when_nothing_resolved():
    assert calc_74_matchup_rate() is None


def test_calc_74_ignores_a_zero_sample_component():
    result = calc_74_matchup_rate(_prob(1.0, 0), _prob(0.25, 400))
    assert result.value == (0.25, 400)


def test_a_flat_average_would_give_a_different_answer():
    """Guards the weighting itself: if precision weighting were dropped for a
    plain mean this test is the one that notices."""
    result = calc_74_matchup_rate(_prob(1.0, 1), _prob(0.2, 999))
    assert result.value.rate < 0.25


# ---------------------------------------------------------------------------
# CALC_75
# ---------------------------------------------------------------------------


def test_the_posterior_is_the_prior_with_no_other_evidence():
    season = _prob(0.26, 500)
    assert calc_75_composite_hit_rate(season).value.rate == pytest.approx(0.26)


def test_evidence_pulls_the_posterior_off_the_prior():
    result = calc_75_composite_hit_rate(_prob(0.25, 100), trending=_prob(0.40, 100))
    assert result.value.rate == pytest.approx(0.325)


def test_a_thin_season_prior_is_moved_further():
    """A hitter with a thin season line gets a weak prior automatically, which
    is the reason the default strength is the season's own sample size."""
    thin = calc_75_composite_hit_rate(_prob(0.25, 10), trending=_prob(0.40, 50))
    thick = calc_75_composite_hit_rate(_prob(0.25, 600), trending=_prob(0.40, 50))
    assert thin.value.rate > thick.value.rate


def test_prior_strength_overrides_the_season_sample_size():
    weak = calc_75_composite_hit_rate(
        _prob(0.25, 600), trending=_prob(0.45, 50), prior_strength=50
    )
    assert weak.value.rate == pytest.approx(0.35)


def test_a_zero_prior_strength_discards_the_prior():
    result = calc_75_composite_hit_rate(
        _prob(0.25, 600), trending=_prob(0.40, 50), prior_strength=0
    )
    assert result.value.rate == pytest.approx(0.40)


def test_a_negative_prior_strength_is_rejected():
    with pytest.raises(ValueError):
        calc_75_composite_hit_rate(_prob(0.25, 600), prior_strength=-1)


def test_the_denominator_reports_the_real_sample_not_the_prior_weight():
    """With prior_strength overridden the weight is not a sample, so #34 must
    still be able to see how much evidence is actually behind the answer."""
    result = calc_75_composite_hit_rate(
        _prob(0.25, 600), trending=_prob(0.40, 50), prior_strength=5
    )
    assert result.value.denominator == 650


def test_the_composite_falls_back_when_the_season_is_missing():
    result = calc_75_composite_hit_rate(None, trending=_prob(0.30, 40))
    assert result.value == (0.30, 40)


def test_the_composite_is_none_with_nothing_at_all():
    assert calc_75_composite_hit_rate() is None


def test_all_three_components_combine():
    result = calc_75_composite_hit_rate(
        _prob(0.25, 100), _prob(0.35, 50), _prob(0.30, 50)
    )
    assert result.value.rate == pytest.approx((25 + 17.5 + 15) / 200)


# ---------------------------------------------------------------------------
# PA_proj and CALC_76
# ---------------------------------------------------------------------------


def test_the_projection_sums_the_two_exponent_keys():
    projected = projected_plate_appearances(
        EXPONENT(Rate(4.6, 1)), EXPONENT(Rate(-0.22, 1761))
    )
    assert projected == pytest.approx(4.38)


def test_a_road_hitter_loses_nothing():
    projected = projected_plate_appearances(
        EXPONENT(Rate(4.6, 1)), EXPONENT(Rate(0.0, 1))
    )
    assert projected == pytest.approx(4.6)


def test_the_ninth_inning_term_is_optional():
    assert projected_plate_appearances(EXPONENT(Rate(4.6, 1))) == pytest.approx(4.6)


def test_no_lineup_spot_means_no_projection():
    assert projected_plate_appearances(None) is None


def test_the_projection_never_goes_negative():
    projected = projected_plate_appearances(
        EXPONENT(Rate(0.1, 1)), EXPONENT(Rate(-5.0, 1))
    )
    assert projected == 0.0


def test_calc_76_applies_the_binomial():
    result = calc_76_game_hit_probability(_prob(0.25, 500), 4.0)
    assert result.value.rate == pytest.approx(1 - 0.75**4)


def test_calc_76_carries_the_evidence_through():
    assert calc_76_game_hit_probability(_prob(0.25, 500), 4.0).value.denominator == 500


def test_no_projected_plate_appearances_means_no_chance():
    assert calc_76_game_hit_probability(_prob(0.25, 500), 0.0).value.rate == 0.0


def test_calc_76_rises_with_more_plate_appearances():
    few = calc_76_game_hit_probability(_prob(0.25, 500), 3.0).value.rate
    many = calc_76_game_hit_probability(_prob(0.25, 500), 5.0).value.rate
    assert many > few


@pytest.mark.parametrize(
    "composite, projected",
    [(None, 4.0), (_prob(0.25, 500), None), (None, None)],
)
def test_calc_76_is_none_without_both_inputs(composite, projected):
    assert calc_76_game_hit_probability(composite, projected) is None


def test_calc_76_rejects_a_negative_projection():
    assert calc_76_game_hit_probability(_prob(0.25, 500), -1.0) is None


# ---------------------------------------------------------------------------
# compute_category_11
# ---------------------------------------------------------------------------


CATEGORY_11_KEYS = {"CALC_72", "CALC_73", "CALC_74", "CALC_75", "CALC_76", "PA_PROJ"}


def test_every_key_is_present_with_no_input():
    assert set(compute_category_11()) == CATEGORY_11_KEYS


def test_absent_input_resolves_every_key_to_none():
    assert set(compute_category_11().values()) == {None}


def test_the_aggregate_wires_the_components_into_the_composite():
    pas = _pas(*[("2026-08-08", i % 4 == 0) for i in range(20)])
    result = compute_category_11(
        pitches=pas,
        platoon=_prob(0.30, 400),
        lineup_spot_pa=EXPONENT(Rate(4.6, 1)),
        today=TODAY,
    )
    assert result["CALC_72"] is not None
    assert result["CALC_74"].value == (0.30, 400)
    assert result["CALC_75"] is not None
    assert result["CALC_76"] is not None


def test_the_final_probability_needs_a_lineup_spot():
    pas = _pas(("2026-08-08", True), ("2026-08-08", False))
    result = compute_category_11(pitches=pas, today=TODAY)
    assert result["CALC_75"] is not None
    assert result["PA_PROJ"] is None
    assert result["CALC_76"] is None


def test_the_aggregate_threads_the_anchor_date():
    pas = _pas(("2026-08-08", True))
    assert compute_category_11(pitches=pas, today=TODAY)["CALC_73"] is not None
    assert compute_category_11(pitches=pas, today=date(2026, 9, 30))["CALC_73"] is None


def test_the_aggregate_takes_no_category_07_input():
    """The tool is scoped to traditional starting pitchers, so the bullpen
    calculators are deliberately not composable into CALC_74."""
    import inspect

    parameters = set(inspect.signature(compute_category_11).parameters)
    assert "bullpen" not in parameters
    assert parameters >= {"bvp", "platoon", "pitch_type"}


def test_a_multiplier_is_not_a_valid_matchup_component():
    """Passing a skill rate where a hit rate belongs is the role-tag error #39
    is open about, and it must not silently average in."""
    with pytest.raises(AttributeError):
        calc_74_matchup_rate(bvp=MULTIPLIER(Rate(0.28, 400)).value)
