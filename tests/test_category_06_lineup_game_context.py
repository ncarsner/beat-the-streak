"""Tests for calculators/category_06_lineup_game_context.py (CALC_41)."""

import pytest

from calculators.common import EXPONENT, PROBABILITY
from calculators.category_06_lineup_game_context import (
    LINEUP_SPOT_PA,
    calc_41_lineup_spot_pa_expectation,
    compute_category_06,
)


@pytest.mark.parametrize("spot", range(1, 10))
def test_every_spot_falls_in_the_roadmap_range(spot):
    """All nine spots project between the ROADMAP's 3.7 and 4.6 endpoints."""
    result = calc_41_lineup_spot_pa_expectation(spot)
    assert isinstance(result, EXPONENT)
    assert 3.70 <= result.value.rate <= 4.60


def test_endpoints_match_the_roadmap():
    assert calc_41_lineup_spot_pa_expectation(1).value.rate == pytest.approx(4.60)
    assert calc_41_lineup_spot_pa_expectation(9).value.rate == pytest.approx(3.70)


def test_leadoff_projects_more_than_ninth():
    first = calc_41_lineup_spot_pa_expectation(1).value.rate
    ninth = calc_41_lineup_spot_pa_expectation(9).value.rate
    assert first > ninth


def test_projection_decreases_monotonically_down_the_order():
    """Each spot later in the order is one turn further from the next PA."""
    rates = [calc_41_lineup_spot_pa_expectation(s).value.rate for s in range(1, 10)]
    assert rates == sorted(rates, reverse=True)
    assert len(set(rates)) == 9  # no two spots collapse onto the same value


@pytest.mark.parametrize("spot", [None, 0, 10, -1, "3", 1.5])
def test_out_of_range_or_malformed_spot_returns_none(spot):
    """No extrapolation past the nine spots a lineup has."""
    assert calc_41_lineup_spot_pa_expectation(spot) is None


def test_role_is_exponent_not_probability():
    """CALC_41 feeds PA_proj, not p_hit.

    The value exceeds 1.0, so blending it into a probability would not merely be
    wrong, it would be nonsense — the role tag is what makes that a type error
    rather than a plausible number.
    """
    result = calc_41_lineup_spot_pa_expectation(1)
    assert isinstance(result, EXPONENT)
    assert not isinstance(result, PROBABILITY)
    assert result.value.rate > 1.0


def test_lookup_table_covers_exactly_the_nine_spots():
    assert set(LINEUP_SPOT_PA) == set(range(1, 10))


def test_compute_category_06_returns_the_key_when_absent():
    """An absent spot yields the key with None, not an omitted key.

    Asserted on `CALC_41` alone rather than on the whole mapping. This test was
    written when `CALC_41` was the only member of the category and pinned the
    return value to exactly ``{"CALC_41": None}``; `CALC_42`-`CALC_46` landing
    widened it legitimately, and the full key set is covered in
    `tests/test_category_06_extended.py`. What matters here is unchanged: the key
    is present and its value is None.
    """
    assert compute_category_06()["CALC_41"] is None
    assert compute_category_06(3)["CALC_41"] is not None
