"""Tests for calculators/category_10_defense_schedule.py."""

from datetime import date

import pytest

from calculators.category_10_defense_schedule import (
    DELTA,
    MULTIPLIER,
    calc_66_infield_defense,
    calc_67_outfield_defense,
    calc_68_umpire_zone,
    calc_69_day_after_night,
    calc_70_travel_displacement,
    calc_71_unfamiliarity,
    compute_category_10,
    great_circle_miles,
    previous_game,
    utc_offset_hours,
)
from tests.conftest import (
    PHOENIX,
    SAN_FRANCISCO,
    TEST_BALLPARKS,
    WRIGLEY,
    YANKEE,
    _sched_game,
)


def _bvp(plate_appearances=None, resolved=True):
    career = (
        None if plate_appearances is None else {"plateAppearances": plate_appearances}
    )
    return {"career": career, "by_season": {}, "resolved": resolved}


# ---------------------------------------------------------------------------
# previous_game
# ---------------------------------------------------------------------------


def test_the_previous_game_is_the_one_before_it_in_date_order():
    log = [
        _sched_game(game_pk=1, game_date="2026-08-01"),
        _sched_game(game_pk=2, game_date="2026-08-02"),
        _sched_game(game_pk=3, game_date="2026-08-03"),
    ]
    assert previous_game(log, 3)["game_pk"] == 2


def test_the_log_is_ordered_regardless_of_input_order():
    log = [
        _sched_game(game_pk=3, game_date="2026-08-03"),
        _sched_game(game_pk=1, game_date="2026-08-01"),
        _sched_game(game_pk=2, game_date="2026-08-02"),
    ]
    assert previous_game(log, 3)["game_pk"] == 2


def test_a_doubleheader_second_game_follows_its_own_first_game():
    """Ordering by date alone would make these two ambiguous, and game two's
    previous game is exactly the fatigue signal these calculators exist for:
    zero travel and a turnaround measured in hours."""
    log = [
        _sched_game(game_pk=1, game_date="2026-08-01"),
        _sched_game(game_pk=2, game_date="2026-08-02", game_number=1),
        _sched_game(game_pk=3, game_date="2026-08-02", game_number=2),
    ]
    assert previous_game(log, 3)["game_pk"] == 2


def test_the_first_game_on_record_has_no_previous_game():
    log = [_sched_game(game_pk=1, game_date="2026-08-01")]
    assert previous_game(log, 1) is None


@pytest.mark.parametrize("log", [None, []])
def test_an_empty_log_has_no_previous_game(log):
    assert previous_game(log, 1) is None


def test_a_game_absent_from_the_log_has_no_previous_game():
    log = [_sched_game(game_pk=1, game_date="2026-08-01")]
    assert previous_game(log, 999) is None


# ---------------------------------------------------------------------------
# CALC_66 / CALC_67 / CALC_68 -- the blocked three
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "calculator", [calc_66_infield_defense, calc_67_outfield_defense]
)
def test_the_oaa_calculators_are_none_without_a_source(calculator):
    """pybaseball 2.0.0 exposes no fielding leaderboard and the Stats API
    publishes no OAA, so these return None in every current code path."""
    assert calculator() is None


@pytest.mark.parametrize(
    "calculator", [calc_66_infield_defense, calc_67_outfield_defense]
)
def test_the_oaa_calculators_report_a_supplied_value(calculator):
    result = calculator(4.0, innings=900)
    assert isinstance(result, DELTA)
    assert result.value == (4.0, 900)


def test_oaa_may_be_negative():
    """A below-average defence is a real reading, and helps the hitter."""
    assert calc_66_infield_defense(-6.0).value.rate == -6.0


def test_zero_oaa_is_a_reading_rather_than_a_missing_value():
    assert calc_67_outfield_defense(0.0) is not None


@pytest.mark.parametrize(
    "index,mean", [(None, None), (1.04, None), (None, 1.0), (1.04, 0)]
)
def test_the_umpire_calculator_needs_both_numbers(index, mean):
    """Statcast carries no umpire column, so neither a per-umpire zone index nor
    a league mean of one is computable yet."""
    assert calc_68_umpire_zone(index, mean) is None


def test_the_umpire_calculator_computes_a_ratio_when_supplied():
    result = calc_68_umpire_zone(1.05, 1.0)
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(1.05)


# ---------------------------------------------------------------------------
# CALC_69
# ---------------------------------------------------------------------------


def test_a_day_game_after_a_night_game_fires_the_indicator():
    previous = _sched_game(game_pk=1, game_date="2026-08-01", day_night="night")
    game = _sched_game(game_pk=2, game_date="2026-08-02", day_night="day")
    assert calc_69_day_after_night(game, previous)["CALC_69"].value.rate == 1.0


@pytest.mark.parametrize(
    "previous_dn,today_dn",
    [("day", "day"), ("night", "night"), ("day", "night")],
)
def test_any_other_combination_does_not_fire(previous_dn, today_dn):
    previous = _sched_game(game_pk=1, game_date="2026-08-01", day_night=previous_dn)
    game = _sched_game(game_pk=2, game_date="2026-08-02", day_night=today_dn)
    assert calc_69_day_after_night(game, previous)["CALC_69"].value.rate == 0.0


def test_a_night_game_two_days_earlier_does_not_fire():
    """The ROADMAP's condition is consecutive days; an off day in between is
    exactly the rest this is meant to detect the absence of."""
    previous = _sched_game(game_pk=1, game_date="2026-07-31", day_night="night")
    game = _sched_game(game_pk=2, game_date="2026-08-02", day_night="day")
    assert calc_69_day_after_night(game, previous)["CALC_69"].value.rate == 0.0


def test_the_turnaround_is_measured_between_first_pitches():
    """Named start-to-start rather than rest on purpose: neither the schedule nor
    Statcast publishes the moment a game ended, so a 10:30pm finish before a
    1:05pm start is 14.5 hours here and nearer 11 hours of real turnaround."""
    previous = _sched_game(game_pk=1, game_date="2026-08-01", start_hour=23)
    game = _sched_game(game_pk=2, game_date="2026-08-02", start_hour=17)
    hours = calc_69_day_after_night(game, previous)["CALC_69_TURNAROUND_HOURS"]
    assert hours.value.rate == pytest.approx(18.0)


def test_the_indicator_is_zero_but_the_turnaround_is_still_reported():
    """A back-to-back that is not day-after-night still has a turnaround."""
    previous = _sched_game(game_pk=1, game_date="2026-08-01", day_night="night")
    game = _sched_game(game_pk=2, game_date="2026-08-02", day_night="night")
    result = calc_69_day_after_night(game, previous)
    assert result["CALC_69"].value.rate == 0.0
    assert result["CALC_69_TURNAROUND_HOURS"] is not None


def test_a_season_opener_has_no_reading():
    game = _sched_game(game_pk=1, game_date="2026-03-26")
    assert calc_69_day_after_night(game, None) == {
        "CALC_69": None,
        "CALC_69_TURNAROUND_HOURS": None,
    }


def test_an_unparseable_date_yields_no_reading():
    previous = _sched_game(game_pk=1, game_date="not-a-date")
    game = _sched_game(game_pk=2, game_date="2026-08-02")
    assert calc_69_day_after_night(game, previous)["CALC_69"] is None


def test_a_missing_start_time_still_yields_the_indicator():
    """The two keys degrade independently."""
    previous = dict(
        _sched_game(game_pk=1, game_date="2026-08-01", day_night="night"),
        start_time=None,
    )
    game = _sched_game(game_pk=2, game_date="2026-08-02", day_night="day")
    result = calc_69_day_after_night(game, previous)
    assert result["CALC_69"].value.rate == 1.0
    assert result["CALC_69_TURNAROUND_HOURS"] is None


# ---------------------------------------------------------------------------
# CALC_70 -- distance
# ---------------------------------------------------------------------------


def test_the_great_circle_distance_matches_a_known_leg():
    """Wrigley Field to Yankee Stadium. Checked against an independent reckoning
    of roughly 713 miles, because a swapped latitude and longitude or a
    degrees-for-radians slip yields a plausible wrong number."""
    assert great_circle_miles(WRIGLEY, YANKEE) == pytest.approx(715.1, abs=1.0)


def test_the_distance_is_symmetric():
    assert great_circle_miles(WRIGLEY, YANKEE) == pytest.approx(
        great_circle_miles(YANKEE, WRIGLEY)
    )


def test_a_home_stand_is_zero_miles():
    assert great_circle_miles(YANKEE, YANKEE) == 0.0


@pytest.mark.parametrize("pair", [(None, YANKEE), (YANKEE, None), (None, None)])
def test_a_missing_venue_is_no_distance_rather_than_zero(pair):
    """Zero would read as a home stand, which is a different claim."""
    assert great_circle_miles(*pair) is None


def test_a_venue_without_coordinates_is_no_distance():
    assert great_circle_miles({"name": "Unknown"}, YANKEE) is None


# ---------------------------------------------------------------------------
# CALC_70 -- time zones
# ---------------------------------------------------------------------------


def test_the_offset_is_resolved_from_the_zone_at_the_games_own_date():
    """Not read from the table's stored offset, which is a generation-time
    snapshot. Oracle Park is -7 in July and -8 in January."""
    assert utc_offset_hours(SAN_FRANCISCO, date(2026, 7, 15)) == -7.0
    assert utc_offset_hours(SAN_FRANCISCO, date(2026, 1, 15)) == -8.0


def test_arizona_does_not_observe_daylight_saving():
    """The case that makes resolving the zone matter rather than merely annoy."""
    assert utc_offset_hours(PHOENIX, date(2026, 7, 15)) == -7.0
    assert utc_offset_hours(PHOENIX, date(2026, 1, 15)) == -7.0


def test_a_summer_trip_to_phoenix_crosses_no_time_zones():
    """San Francisco and Phoenix are both -7 in July, and one apart in April.
    A stored offset gets exactly one of those two right."""
    july = utc_offset_hours(PHOENIX, date(2026, 7, 15)) - utc_offset_hours(
        SAN_FRANCISCO, date(2026, 7, 15)
    )
    winter = utc_offset_hours(PHOENIX, date(2026, 1, 15)) - utc_offset_hours(
        SAN_FRANCISCO, date(2026, 1, 15)
    )
    assert july == 0.0
    assert winter == 1.0


@pytest.mark.parametrize("venue", [None, {}, {"timezone_id": "Not/AZone"}])
def test_an_unusable_zone_yields_no_offset(venue):
    assert utc_offset_hours(venue, date(2026, 7, 15)) is None


def test_no_date_yields_no_offset():
    assert utc_offset_hours(YANKEE, None) is None


# ---------------------------------------------------------------------------
# CALC_70 -- the calculator
# ---------------------------------------------------------------------------


def _leg(from_venue, to_venue, from_date="2026-08-02", to_date="2026-08-03"):
    previous = _sched_game(game_pk=1, game_date=from_date, venue_id=from_venue)
    game = _sched_game(game_pk=2, game_date=to_date, venue_id=to_venue)
    return calc_70_travel_displacement(game, previous, TEST_BALLPARKS)


def test_a_real_travel_leg_reports_distance_shift_and_rest():
    """Chicago to New York with no off day, the 2026-08-02 to 2026-08-03 leg."""
    result = _leg(17, 3313)
    assert result["CALC_70"].value.rate == pytest.approx(715.1, abs=1.0)
    assert result["CALC_70_TZ_SHIFT"].value.rate == pytest.approx(1.0)
    assert result["CALC_70_DAYS_REST"].value.rate == 1.0


def test_travelling_west_gives_a_negative_shift():
    """Signed, positive travelling east."""
    assert _leg(3313, 17)["CALC_70_TZ_SHIFT"].value.rate == pytest.approx(-1.0)


def test_a_home_stand_reports_zero_miles_and_no_shift():
    result = _leg(3313, 3313)
    assert result["CALC_70"].value.rate == 0.0
    assert result["CALC_70_TZ_SHIFT"].value.rate == 0.0


def test_an_off_day_shows_as_two_days_rest():
    result = _leg(17, 3313, from_date="2026-08-01", to_date="2026-08-03")
    assert result["CALC_70_DAYS_REST"].value.rate == 2.0


def test_a_doubleheader_shows_as_zero_days_rest():
    result = _leg(3313, 3313, from_date="2026-08-03", to_date="2026-08-03")
    assert result["CALC_70_DAYS_REST"].value.rate == 0.0


def test_a_venue_missing_from_the_table_yields_no_distance():
    result = _leg(17, 99999)
    assert result["CALC_70"] is None
    assert result["CALC_70_DAYS_REST"] is not None


def test_a_season_opener_has_no_travel_leg():
    game = _sched_game(game_pk=1)
    assert calc_70_travel_displacement(game, None, TEST_BALLPARKS) == {
        "CALC_70": None,
        "CALC_70_TZ_SHIFT": None,
        "CALC_70_DAYS_REST": None,
    }


def test_no_threshold_for_cross_country_is_applied_here():
    """The ROADMAP's rule is over three measurements and this module reports the
    measurements. A mileage threshold would be a league constant #38 blocks."""
    short = _leg(3313, 17)["CALC_70"].value.rate
    assert short > 0
    assert isinstance(_leg(3313, 17)["CALC_70"], DELTA)


# ---------------------------------------------------------------------------
# CALC_71
# ---------------------------------------------------------------------------


def test_a_pair_that_has_never_met_reports_one():
    result = calc_71_unfamiliarity(_bvp(plate_appearances=None))
    assert isinstance(result, DELTA)
    assert result.value == (1.0, 0)


def test_a_pair_with_history_reports_zero():
    assert calc_71_unfamiliarity(_bvp(plate_appearances=9)).value == (0.0, 9)


def test_a_resolved_zero_plate_appearance_line_also_reports_one():
    """A career line present but empty is still no history."""
    assert calc_71_unfamiliarity(_bvp(plate_appearances=0)).value.rate == 1.0


def test_a_failed_fetch_reports_nothing_rather_than_a_first_meeting():
    """The whole reason `resolved` exists. A dropped request produces the same
    null career line as a genuine first meeting, and this is the one calculator
    for which those are opposite answers rather than both "no sample"."""
    assert calc_71_unfamiliarity(_bvp(plate_appearances=None, resolved=False)) is None


@pytest.mark.parametrize("bvp", [None, {}])
def test_an_absent_payload_reports_nothing(bvp):
    assert calc_71_unfamiliarity(bvp) is None


def test_a_payload_predating_the_resolved_flag_reports_nothing():
    """Fails closed rather than open: a cached payload without the key is not
    evidence that the pair has never met."""
    assert calc_71_unfamiliarity({"career": None, "by_season": {}}) is None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


EXPECTED_KEYS = {
    "CALC_66",
    "CALC_67",
    "CALC_68",
    "CALC_69",
    "CALC_69_TURNAROUND_HOURS",
    "CALC_70",
    "CALC_70_TZ_SHIFT",
    "CALC_70_DAYS_REST",
    "CALC_71",
}


def test_absent_input_resolves_every_key_to_none_rather_than_omitting_it():
    result = compute_category_10()
    assert set(result) == EXPECTED_KEYS
    assert all(value is None for value in result.values())


def test_the_key_set_does_not_depend_on_what_was_fetched():
    log = [
        _sched_game(game_pk=1, game_date="2026-08-02", venue_id=17, day_night="night"),
        _sched_game(game_pk=2, game_date="2026-08-03", venue_id=3313, day_night="day"),
    ]
    result = compute_category_10(
        game=log[1],
        game_log=log,
        ballparks=TEST_BALLPARKS,
        bvp=_bvp(plate_appearances=9),
    )
    assert set(result) == EXPECTED_KEYS
    assert result["CALC_69"].value.rate == 1.0
    assert result["CALC_70"].value.rate == pytest.approx(715.1, abs=1.0)
    assert result["CALC_71"].value.rate == 0.0
    assert result["CALC_66"] is None


def test_the_previous_game_is_resolved_from_the_log_not_passed_in():
    """A caller cannot accidentally supply a previous game from another club."""
    log = [
        _sched_game(game_pk=1, game_date="2026-08-01", day_night="night"),
        _sched_game(game_pk=2, game_date="2026-08-02", day_night="day"),
    ]
    assert compute_category_10(game=log[1], game_log=log)["CALC_69"].value.rate == 1.0
