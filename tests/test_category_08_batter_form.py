"""Tests for the Category 8 batter-form calculators."""

from datetime import date, timedelta

import pytest

from calculators.common import DAYS, DELTA, MULTIPLIER, PROBABILITY
from calculators.category_08_batter_form import (
    BABIP_EXCLUDED_EVENTS,
    SWEET_SPOT_ANGLE_DEGREES,
    babip,
    calc_52_recent_game_form,
    calc_53_seven_day_hit_rate,
    calc_54_fourteen_day_hit_rate,
    calc_55_contact_quality_trend,
    calc_56_active_hit_streak,
    calc_57_luck_delta,
    calc_58_babip_regression,
    calc_59_sweet_spot_trend,
    compute_category_08,
    game_log,
    plate_appearances,
)
from tests.conftest import _form_pa, _form_pitch

TODAY = date(2026, 7, 15)


def _day(offset):
    """A game date *offset* days before TODAY, as a Statcast date string."""
    return (TODAY - timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------------------
# plate_appearances -- the shared reduction
# ---------------------------------------------------------------------------


def test_plate_appearances_reduces_to_the_terminal_pitch():
    pitches = [
        _form_pitch(at_bat_number=1, pitch_number=1, description="foul"),
        _form_pitch(at_bat_number=1, pitch_number=2, description="called_strike"),
        _form_pitch(
            at_bat_number=1,
            pitch_number=3,
            events="single",
            description="hit_into_play",
        ),
    ]
    pas = plate_appearances(pitches)
    assert len(pas) == 1
    assert pas[0]["events"] == "single"


@pytest.mark.parametrize("game_type", ["S", "E", "A"])
def test_plate_appearances_drops_non_competitive_games(game_type):
    """Statcast season pulls include spring training, and it was 7.8% of the probe
    batter's pitches. At a three-game window that is most of the sample."""
    pitches = [
        _form_pa(hit=True, game_type="R", game_pk=1),
        _form_pa(hit=True, game_type=game_type, game_pk=2),
    ]
    pas = plate_appearances(pitches)
    assert len(pas) == 1
    assert pas[0]["game_pk"] == 1


def test_plate_appearances_keeps_postseason_games():
    """Postseason codes are deliberately not in the excluded set: those are real
    competitive games and real recent form."""
    for code in ("F", "D", "L", "W"):
        assert len(plate_appearances([_form_pa(hit=True, game_type=code)])) == 1


def test_plate_appearances_are_sorted_oldest_first():
    pitches = [
        _form_pa(game_date=_day(1), game_pk=3),
        _form_pa(game_date=_day(9), game_pk=1),
        _form_pa(game_date=_day(5), game_pk=2),
    ]
    assert [pa["game_pk"] for pa in plate_appearances(pitches)] == [1, 2, 3]


# ---------------------------------------------------------------------------
# game_log
# ---------------------------------------------------------------------------


def test_game_log_counts_hits_and_plate_appearances_per_game():
    pas = [
        _form_pa(hit=True, game_pk=1, at_bat_number=1),
        _form_pa(events="strikeout", game_pk=1, at_bat_number=2),
        _form_pa(hit=True, game_pk=1, at_bat_number=3),
    ]
    log = game_log(plate_appearances(pas))
    assert log == [{"game_pk": 1, "game_date": "2026-07-01", "hits": 2, "pa": 3}]


def test_game_log_keeps_a_doubleheader_as_two_games():
    """Keyed on game_pk, not game_date. `apply_window(GAMES(n))` dedups by date
    string and would collapse these into one, silently widening a 3-game window
    by a day."""
    pas = [
        _form_pa(hit=True, game_date="2026-07-01", game_pk=1),
        _form_pa(hit=True, game_date="2026-07-01", game_pk=2),
    ]
    assert len(game_log(plate_appearances(pas))) == 2


# ---------------------------------------------------------------------------
# CALC_52
# ---------------------------------------------------------------------------


def _games(*hits_per_game, start_offset=10):
    """One plate appearance per hit plus one out, for each game in order."""
    pitches = []
    for i, hits in enumerate(hits_per_game):
        pk = i + 1
        day = _day(start_offset - i)
        for n in range(hits):
            pitches.append(
                _form_pa(hit=True, game_pk=pk, game_date=day, at_bat_number=n + 1)
            )
        pitches.append(
            _form_pa(
                events="field_out", game_pk=pk, game_date=day, at_bat_number=hits + 1
            )
        )
    return pitches


def test_calc_52_is_a_per_plate_appearance_hit_rate():
    """Not the "hit in last 3 games, Y/N" frequency the ROADMAP's shorthand
    suggests: that is the model's output scale, and feeding it back in as a p_hit
    input would apply the binomial twice."""
    result = calc_52_recent_game_form(_games(1, 1, 1), games=3)
    assert isinstance(result["CALC_52"], PROBABILITY)
    assert result["CALC_52"].value.rate == pytest.approx(0.5)
    assert result["CALC_52"].value.denominator == 6


def test_calc_52_uses_only_the_last_n_games():
    result = calc_52_recent_game_form(_games(0, 0, 0, 2, 2, 2), games=3)
    assert result["CALC_52"].value.rate == pytest.approx(6 / 9)
    assert result["CALC_52_MULTI_HIT"].value.rate == pytest.approx(1.0)


def test_calc_52_multi_hit_is_a_per_game_frequency():
    result = calc_52_recent_game_form(_games(2, 1, 0), games=3)
    assert isinstance(result["CALC_52_MULTI_HIT"], MULTIPLIER)
    assert result["CALC_52_MULTI_HIT"].value.rate == pytest.approx(1 / 3)
    assert result["CALC_52_MULTI_HIT"].value.denominator == 3


def test_calc_52_returns_none_on_an_empty_log():
    assert calc_52_recent_game_form([]) == {"CALC_52": None, "CALC_52_MULTI_HIT": None}


# ---------------------------------------------------------------------------
# CALC_53 / CALC_54 -- calendar windows
# ---------------------------------------------------------------------------


def test_calc_53_counts_only_the_last_seven_days():
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1),
        _form_pa(events="field_out", game_date=_day(3), game_pk=2),
        _form_pa(hit=True, game_date=_day(20), game_pk=3),
    ]
    result = calc_53_seven_day_hit_rate(pitches, today=TODAY)
    assert result.value.rate == pytest.approx(0.5)
    assert result.value.denominator == 2


def test_calc_54_reaches_further_back_than_calc_53():
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1),
        _form_pa(events="field_out", game_date=_day(10), game_pk=2),
    ]
    assert calc_53_seven_day_hit_rate(pitches, today=TODAY).value.denominator == 1
    assert calc_54_fourteen_day_hit_rate(pitches, today=TODAY).value.denominator == 2


def test_the_day_window_excludes_today():
    """`DAYS(n)` ends yesterday because today's game has not been played."""
    pitches = [_form_pa(hit=True, game_date=TODAY.isoformat(), game_pk=1)]
    assert calc_53_seven_day_hit_rate(pitches, today=TODAY) is None


def test_an_empty_window_returns_none_not_a_zero_rate():
    """Absence of evidence, not evidence of a slump."""
    pitches = [_form_pa(hit=True, game_date=_day(40), game_pk=1)]
    assert calc_53_seven_day_hit_rate(pitches, today=TODAY) is None
    assert calc_54_fourteen_day_hit_rate(pitches, today=TODAY) is None


def test_today_is_threaded_rather_than_read_from_the_clock():
    """The validation harness (#35) has to evaluate against past dates, which is
    impossible if a calculator reads the clock internally."""
    pitches = [_form_pa(hit=True, game_date="2026-03-10", game_pk=1)]
    assert calc_53_seven_day_hit_rate(pitches, today=date(2026, 3, 12)) is not None
    assert calc_53_seven_day_hit_rate(pitches, today=date(2026, 7, 15)) is None


# ---------------------------------------------------------------------------
# CALC_55
# ---------------------------------------------------------------------------


def test_calc_55_reports_xwoba_and_hard_hit_over_batted_balls():
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1, launch_speed=100.0, xwoba=1.2),
        _form_pa(
            events="field_out",
            description="hit_into_play",
            game_date=_day(3),
            game_pk=2,
            launch_speed=80.0,
            xwoba=0.0,
        ),
        _form_pa(events="strikeout", game_date=_day(3), game_pk=2, at_bat_number=2),
    ]
    result = calc_55_contact_quality_trend(pitches, today=TODAY)
    assert result["CALC_55_HARD_HIT"].value.rate == pytest.approx(0.5)
    assert result["CALC_55_HARD_HIT"].value.denominator == 2
    assert result["CALC_55_XWOBA"].value.rate == pytest.approx(0.6)


def test_calc_55_is_multiplier_not_probability():
    """xwOBA is not bounded by 1.0 and a hard-hit rate runs near .40; neither is
    on the p_hit scale."""
    pitches = [_form_pa(hit=True, game_date=_day(2), launch_speed=100.0, xwoba=1.9)]
    result = calc_55_contact_quality_trend(pitches, today=TODAY)
    assert isinstance(result["CALC_55_XWOBA"], MULTIPLIER)
    assert isinstance(result["CALC_55_HARD_HIT"], MULTIPLIER)
    assert result["CALC_55_XWOBA"].value.rate > 1.0


def test_calc_55_skips_batted_balls_with_no_measurement():
    """A null launch speed is no reading, never a soft-hit ball."""
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1, launch_speed=100.0),
        _form_pa(
            hit=True, game_date=_day(2), game_pk=1, at_bat_number=2, launch_speed=None
        ),
    ]
    result = calc_55_contact_quality_trend(pitches, today=TODAY)
    assert result["CALC_55_HARD_HIT"].value.denominator == 1
    assert result["CALC_55_HARD_HIT"].value.rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CALC_56
# ---------------------------------------------------------------------------


def test_calc_56_counts_consecutive_games_with_a_hit():
    result = calc_56_active_hit_streak(_games(0, 1, 1, 1))
    assert isinstance(result, DELTA)
    assert result.value.rate == pytest.approx(3.0)
    assert result.value.denominator == 4


def test_calc_56_breaks_the_streak_on_a_hitless_game():
    assert calc_56_active_hit_streak(_games(1, 1, 0, 1)).value.rate == pytest.approx(
        1.0
    )


def test_a_hitless_most_recent_game_is_a_streak_of_zero_not_none():
    """Zero is a real measurement. None means no games on record, and the two are
    different facts about a hitter."""
    result = calc_56_active_hit_streak(_games(1, 1, 0))
    assert result is not None
    assert result.value.rate == pytest.approx(0.0)


def test_calc_56_returns_none_on_an_empty_game_log():
    assert calc_56_active_hit_streak([]) is None


def test_calc_56_ignores_spring_training_games_in_the_walk():
    pitches = _games(1, 1)
    for p in pitches:
        p["game_type"] = "S"
    assert calc_56_active_hit_streak(pitches) is None


# ---------------------------------------------------------------------------
# CALC_57 -- the denominator trap
# ---------------------------------------------------------------------------


def test_calc_57_puts_both_legs_over_the_same_denominator():
    """The trap: mean xBA over batted balls runs near .41 while a hit rate runs
    near .21, so subtracting them yields about -.20 for every hitter alive. Here a
    strikeout contributes 0 to both numerators and 1 to the shared denominator."""
    pitches = [
        _form_pa(hit=True, game_pk=1, at_bat_number=1, xba=0.500),
        _form_pa(
            events="field_out",
            description="hit_into_play",
            game_pk=1,
            at_bat_number=2,
            xba=0.100,
        ),
        _form_pa(events="strikeout", game_pk=1, at_bat_number=3),
    ]
    result = calc_57_luck_delta(pitches)
    # actual 1/3, expected (0.5 + 0.1)/3 = 0.2
    assert result.value.rate == pytest.approx(1 / 3 - 0.2)
    assert result.value.denominator == 3


def test_calc_57_is_negative_for_an_unlucky_hitter():
    """High expected, low actual: the ROADMAP's positive regression candidate."""
    pitches = [
        _form_pa(
            events="field_out",
            description="hit_into_play",
            game_pk=1,
            at_bat_number=n,
            xba=0.800,
        )
        for n in range(1, 4)
    ]
    assert calc_57_luck_delta(pitches).value.rate == pytest.approx(-0.8)


def test_calc_57_drops_batted_balls_with_no_xba_estimate():
    """An unestimated batted ball that was a hit would manufacture good luck: it
    counts in the actual numerator and contributes nothing to the expected one."""
    pitches = [
        _form_pa(hit=True, game_pk=1, at_bat_number=1, xba=0.400),
        _form_pa(hit=True, game_pk=1, at_bat_number=2, xba=None),
    ]
    result = calc_57_luck_delta(pitches)
    assert result.value.denominator == 1
    assert result.value.rate == pytest.approx(1.0 - 0.4)


def test_calc_57_keeps_strikeouts_which_carry_no_xba_by_definition():
    """A strikeout is zero expected hits, which is a measurement, not a gap."""
    pitches = [
        _form_pa(hit=True, game_pk=1, at_bat_number=1, xba=0.400),
        _form_pa(events="strikeout", game_pk=1, at_bat_number=2),
    ]
    assert calc_57_luck_delta(pitches).value.denominator == 2


def test_calc_57_accepts_a_window():
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1, xba=0.300),
        _form_pa(hit=True, game_date=_day(40), game_pk=2, xba=0.900),
    ]
    assert calc_57_luck_delta(pitches).value.denominator == 2
    assert (
        calc_57_luck_delta(pitches, window=DAYS(7), today=TODAY).value.denominator == 1
    )


def test_calc_57_returns_none_with_nothing_measurable():
    assert calc_57_luck_delta([]) is None


# ---------------------------------------------------------------------------
# CALC_58
# ---------------------------------------------------------------------------


def test_babip_excludes_home_runs_from_both_halves():
    """No defense could have caught it, which is the premise of the statistic."""
    pas = plate_appearances(
        [
            _form_pa(hit=True, game_pk=1, at_bat_number=1),
            _form_pa(events="home_run", game_pk=1, at_bat_number=2),
            _form_pa(
                events="field_out",
                description="hit_into_play",
                game_pk=1,
                at_bat_number=3,
            ),
        ]
    )
    result = babip(pas)
    assert result.rate == pytest.approx(0.5)
    assert result.denominator == 2


@pytest.mark.parametrize("event", sorted(BABIP_EXCLUDED_EVENTS))
def test_babip_denominator_excludes_the_documented_events(event):
    pas = plate_appearances(
        [_form_pa(events=event, description="hit_into_play", game_pk=1)]
    )
    assert babip(pas) is None


def test_babip_ignores_plate_appearances_that_never_put_a_ball_in_play():
    pas = plate_appearances(
        [
            _form_pa(events="strikeout", game_pk=1, at_bat_number=1),
            _form_pa(events="walk", game_pk=1, at_bat_number=2),
        ]
    )
    assert babip(pas) is None


def test_calc_58_compares_recent_against_the_season_baseline():
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1, at_bat_number=1),
        _form_pa(hit=True, game_date=_day(3), game_pk=2, at_bat_number=1),
    ] + [
        _form_pa(
            events="field_out",
            description="hit_into_play",
            game_date=_day(40),
            game_pk=3,
            at_bat_number=n,
        )
        for n in range(1, 3)
    ]
    result = calc_58_babip_regression(pitches, today=TODAY)
    assert result["CALC_58"].value.rate == pytest.approx(1.0)
    assert result["CALC_58_SEASON"].value.rate == pytest.approx(0.5)
    assert result["CALC_58_DELTA"].value.rate == pytest.approx(0.5)


def test_calc_58_delta_is_none_when_either_leg_is_missing():
    """A difference against a missing baseline is not a small number, it is no
    number."""
    pitches = [_form_pa(hit=True, game_date=_day(40), game_pk=1)]
    result = calc_58_babip_regression(pitches, today=TODAY)
    assert result["CALC_58"] is None
    assert result["CALC_58_SEASON"] is not None
    assert result["CALC_58_DELTA"] is None


# ---------------------------------------------------------------------------
# CALC_59
# ---------------------------------------------------------------------------


def _batted(angle, **kwargs):
    return _form_pa(
        events="field_out", description="hit_into_play", launch_angle=angle, **kwargs
    )


@pytest.mark.parametrize(
    "angle,inside",
    [(8.0, True), (32.0, True), (20.0, True), (7.9, False), (32.1, False)],
)
def test_calc_59_band_is_inclusive_at_both_ends(angle, inside):
    low, high = SWEET_SPOT_ANGLE_DEGREES
    assert (low <= angle <= high) is inside
    result = calc_59_sweet_spot_trend([_batted(angle, game_pk=1)])
    assert result.value.rate == pytest.approx(1.0 if inside else 0.0)


def test_calc_59_windows_by_plate_appearance_not_by_pitch():
    """`PLATE_APPEARANCES(n)` requires one record per plate appearance. Handing it
    raw pitch rows would window the last 30 pitches, roughly 8 plate appearances,
    and return a plausible number over a quarter of the intended sample."""
    old = [
        _batted(45.0, game_pk=1, at_bat_number=n, game_date=_day(30))
        for n in range(1, 11)
    ]
    recent = [
        _batted(20.0, game_pk=2, at_bat_number=n, game_date=_day(2))
        for n in range(1, 6)
    ]
    result = calc_59_sweet_spot_trend(old + recent, window_size=5)
    assert result.value.denominator == 5
    assert result.value.rate == pytest.approx(1.0)


def test_calc_59_denominator_is_batted_balls_within_the_plate_appearance_window():
    """The window is plate appearances, the rate is per batted ball. 30 plate
    appearances typically yields 15 to 20 launch-angle measurements."""
    pitches = [_batted(20.0, game_pk=1, at_bat_number=1)] + [
        _form_pa(events="strikeout", game_pk=1, at_bat_number=n) for n in range(2, 6)
    ]
    result = calc_59_sweet_spot_trend(pitches, window_size=30)
    assert result.value.denominator == 1


def test_calc_59_returns_none_without_a_launch_angle_reading():
    assert calc_59_sweet_spot_trend([_form_pa(events="strikeout", game_pk=1)]) is None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


CATEGORY_08_KEYS = {
    "CALC_52",
    "CALC_52_MULTI_HIT",
    "CALC_53",
    "CALC_54",
    "CALC_55_XWOBA",
    "CALC_55_HARD_HIT",
    "CALC_56",
    "CALC_57",
    "CALC_58",
    "CALC_58_SEASON",
    "CALC_58_DELTA",
    "CALC_59",
}


@pytest.mark.parametrize("pitches", [None, [], [_form_pa(hit=True)]])
def test_compute_category_08_always_emits_the_full_key_set(pitches):
    assert set(compute_category_08(pitches, today=TODAY)) == CATEGORY_08_KEYS


def test_compute_category_08_resolves_every_key_to_none_without_input():
    assert set(compute_category_08().values()) == {None}


def test_compute_category_08_populates_every_key_on_a_full_season():
    pitches = []
    for i in range(6):
        day = _day(12 - i * 2)
        pitches += [
            _form_pa(
                hit=True,
                game_date=day,
                game_pk=i + 1,
                at_bat_number=1,
                launch_speed=100.0,
                launch_angle=20.0,
                xba=0.5,
                xwoba=1.1,
            ),
            _form_pa(
                events="field_out",
                description="hit_into_play",
                game_date=day,
                game_pk=i + 1,
                at_bat_number=2,
                launch_speed=80.0,
                launch_angle=45.0,
                xba=0.1,
                xwoba=0.0,
            ),
            _form_pa(events="strikeout", game_date=day, game_pk=i + 1, at_bat_number=3),
        ]
    result = compute_category_08(pitches, today=TODAY)
    assert set(result) == CATEGORY_08_KEYS
    assert all(v is not None for v in result.values()), {
        k: v for k, v in result.items() if v is None
    }


def test_every_category_08_value_is_role_tagged():
    pitches = [
        _form_pa(hit=True, game_date=_day(2), game_pk=1, launch_angle=20.0, xba=0.4)
    ]
    for value in compute_category_08(pitches, today=TODAY).values():
        assert value is None or isinstance(value, (PROBABILITY, MULTIPLIER, DELTA))


def test_only_the_per_plate_appearance_hit_rates_are_probabilities():
    """Category 4's stricter convention: PROBABILITY means p_hit-scale. A .56
    hard-hit rate under the same tag would invite a direct blend."""
    pitches = []
    for i in range(3):
        day = _day(6 - i)
        pitches += [
            _form_pa(
                hit=True,
                game_date=day,
                game_pk=i + 1,
                at_bat_number=1,
                launch_speed=100.0,
                launch_angle=20.0,
                xba=0.5,
                xwoba=1.1,
            ),
            _form_pa(events="strikeout", game_date=day, game_pk=i + 1, at_bat_number=2),
        ]
    result = compute_category_08(pitches, today=TODAY)
    probabilities = {k for k, v in result.items() if isinstance(v, PROBABILITY)}
    assert probabilities == {"CALC_52", "CALC_53", "CALC_54"}
