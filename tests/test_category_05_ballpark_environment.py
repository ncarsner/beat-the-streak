"""Tests for calculators/category_05_ballpark_environment.py."""

import math

import pytest

from calculators.category_05_ballpark_environment import (
    DELTA,
    MIN_OPEN_ROOF_SHARE,
    MULTIPLIER,
    NEUTRAL_TEMPERATURE_F,
    PROBABILITY,
    SECTOR_NAMES,
    STANDARD_AIR_DENSITY,
    air_balls,
    air_density,
    calc_31_ballpark_hit_factor,
    calc_32_ballpark_handedness_factor,
    calc_33_geometry_match,
    calc_34_temperature,
    calc_35_air_density_index,
    calc_36_wind_effect,
    calc_37_day_night,
    calc_38_home_away,
    calc_39_roof_status,
    calc_40_venue_familiarity,
    compute_category_05,
    league_mean_fences,
    open_roof_index,
    parse_condition,
    parse_temperature_f,
    parse_wind,
    roof_state,
    sector_for_angle,
    spray_angle,
    spray_distribution,
    temperature_tier,
)
from tests.conftest import _air_ball, _ballpark, _env_pitch, _park_factor

VENUE = "3313"
OTHER_VENUE = "17"


def _parks(**kwargs):
    return {VENUE: _ballpark(**kwargs)}


def _factors(**kwargs):
    return {VENUE: _park_factor(**kwargs)}


# ---------------------------------------------------------------------------
# Free-text parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weather,expected",
    [
        ("89 degrees, Partly Cloudy.", 89.0),
        ("72 degrees, Dome.", 72.0),
        ("-4 degrees, Clear.", -4.0),
        ("Partly Cloudy.", None),
        ("", None),
        (None, None),
    ],
)
def test_temperature_is_read_out_of_the_weather_prose(weather, expected):
    assert parse_temperature_f(weather) == expected


@pytest.mark.parametrize(
    "weather,expected",
    [
        ("89 degrees, Partly Cloudy.", "partly cloudy"),
        ("70 degrees, Roof Closed.", "roof closed"),
        ("72 degrees, Dome.", "dome"),
        ("89 degrees", None),
        (None, None),
    ],
)
def test_condition_is_read_out_of_the_weather_prose(weather, expected):
    assert parse_condition(weather) == expected


@pytest.mark.parametrize(
    "wind,expected",
    [
        ("11 mph, Out To RF.", (11.0, "out to rf")),
        ("8 mph, L To R.", (8.0, "l to r")),
        ("0 mph, None.", (0.0, "none")),
        ("12 mph, In From CF.", (12.0, "in from cf")),
        ("Out To RF.", None),
        ("11 mph", None),
        (None, None),
    ],
)
def test_wind_is_read_out_of_the_wind_prose(wind, expected):
    assert parse_wind(wind) == expected


# ---------------------------------------------------------------------------
# Spray geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "angle,sector",
    [
        (-40.0, "left_line"),
        (-27.0, "left_center"),
        (-10.0, "left_center"),
        (0.0, "center"),
        (8.9, "center"),
        (9.0, "right_center"),
        (30.0, "right_line"),
        (45.0, "right_line"),
    ],
)
def test_every_fair_angle_lands_in_exactly_one_sector(angle, sector):
    assert sector_for_angle(angle) == sector


def test_a_foul_angle_belongs_to_no_sector():
    assert sector_for_angle(50.0) is None
    assert sector_for_angle(-50.0) is None


@pytest.mark.parametrize("angle", [-44.0, -20.0, 0.0, 20.0, 44.0])
def test_the_solved_angle_round_trips_through_the_coordinates(angle):
    """`spray_angle` is the inverse of the fixture builder, so a fixture that
    names a sector really produces one."""
    assert spray_angle(_env_pitch(angle=angle)) == pytest.approx(angle)


def test_negative_is_left_field_and_positive_is_right_field():
    """The sign convention, asserted directly rather than left implicit.

    A flipped sign turns every pull into an oppo and leaves every downstream
    number plausible, which is why it is validated in both directions here and
    was validated against two real hitters before the module was written.
    """
    assert sector_for_angle(spray_angle(_env_pitch(angle=-35.0))) == "left_line"
    assert sector_for_angle(spray_angle(_env_pitch(angle=35.0))) == "right_line"


@pytest.mark.parametrize("hc_x,hc_y", [(None, 100.0), (100.0, None), (None, None)])
def test_an_untracked_landing_point_has_no_angle(hc_x, hc_y):
    assert spray_angle(_env_pitch(hc_x=hc_x, hc_y=hc_y)) is None


def test_an_angle_outside_fair_territory_is_rejected_rather_than_clamped():
    """A popup a few feet off the plate can solve to 60 degrees. Clamping it to
    45 would file a mis-measured ball down the right-field line."""
    assert spray_angle(_env_pitch(angle=70.0)) is None


def test_ground_balls_are_excluded_from_the_spray_distribution():
    """Fence distance and wind do not act on a ball that stays in the infield."""
    pitches = [
        _air_ball(-35.0, at_bat_number=1),
        _env_pitch(
            at_bat_number=2,
            description="hit_into_play",
            bb_type="ground_ball",
            angle=35.0,
        ),
    ]
    shares, tracked = spray_distribution(pitches)
    assert tracked == 1
    assert shares["left_line"] == 1.0


def test_spring_training_is_excluded_from_the_spray_distribution():
    pitches = [
        _air_ball(-35.0, at_bat_number=1),
        _air_ball(35.0, at_bat_number=2, game_type="S"),
    ]
    shares, tracked = spray_distribution(pitches)
    assert tracked == 1
    assert shares["right_line"] == 0.0


def test_the_spray_distribution_sums_to_one():
    pitches = [
        _air_ball(a, at_bat_number=i) for i, a in enumerate([-40, -10, 0, 20, 40])
    ]
    shares, tracked = spray_distribution(pitches)
    assert tracked == 5
    assert sum(shares.values()) == pytest.approx(1.0)


def test_no_tracked_air_ball_is_no_sample_rather_than_a_uniform_spray():
    shares, tracked = spray_distribution([])
    assert (shares, tracked) == ({}, 0)


def test_air_balls_reduces_to_one_record_per_plate_appearance():
    """A four-pitch plate appearance ending in a fly ball is one batted ball."""
    pitches = [
        _env_pitch(at_bat_number=7, pitch_number=n, description="ball")
        for n in (1, 2, 3)
    ]
    pitches.append(
        _env_pitch(
            at_bat_number=7,
            pitch_number=4,
            description="hit_into_play",
            bb_type="fly_ball",
            angle=0.0,
        )
    )
    assert len(air_balls(pitches)) == 1


# ---------------------------------------------------------------------------
# CALC_31 / CALC_32 -- park factors
# ---------------------------------------------------------------------------


def test_calc_31_puts_the_index_on_a_multiplier_scale():
    """104 means 4 percent above neutral, so the value is 1.04."""
    result = calc_31_ballpark_hit_factor(_factors(all_index=104), VENUE)
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(1.04)


def test_calc_31_carries_savants_own_sample_size():
    """The one genuine sample size in this category's game-level half."""
    result = calc_31_ballpark_hit_factor(_factors(n_pa=52026), VENUE)
    assert result.value.denominator == 52026


def test_calc_31_accepts_an_integer_venue_id():
    """Callers hold the venue id as an int; the table is keyed by string."""
    assert calc_31_ballpark_hit_factor(_factors(all_index=110), 3313) is not None


def test_calc_31_is_none_for_a_venue_the_table_does_not_cover():
    """Real case, not a defect: Savant carried 29 of 30 venues on 2026-08-09."""
    assert calc_31_ballpark_hit_factor(_factors(), OTHER_VENUE) is None


@pytest.mark.parametrize("index_key", ["index_1b", "index_2b", "index_3b"])
def test_calc_31_returns_none_for_a_component_index_the_table_omits(index_key):
    assert calc_31_ballpark_hit_factor(_factors(), VENUE, index_key) is None


@pytest.mark.parametrize("side,expected", [("L", 0.93), ("R", 1.06)])
def test_calc_32_selects_the_batters_own_hand(side, expected):
    result = calc_32_ballpark_handedness_factor(
        _factors(left=93, right=106), VENUE, side
    )
    assert result.value.rate == pytest.approx(expected)


@pytest.mark.parametrize("side", [None, "S", "", "l"])
def test_calc_32_refuses_to_guess_a_batting_side(side):
    """A switch hitter has no fixed hand. Falling back to the all-batters factor
    would silently return CALC_31 under CALC_32's key."""
    assert calc_32_ballpark_handedness_factor(_factors(), VENUE, side) is None


# ---------------------------------------------------------------------------
# CALC_33 -- geometry
# ---------------------------------------------------------------------------


def test_league_mean_fences_averages_each_sector_independently():
    parks = {
        "1": _ballpark(fences=(300, 380, 400, 380, 300)),
        "2": _ballpark(fences=(340, 400, 420, 400, 340)),
    }
    assert league_mean_fences(parks) == [320.0, 390.0, 410.0, 390.0, 320.0]


def test_league_mean_fences_ignores_a_park_with_a_malformed_fence_list():
    parks = {
        "1": _ballpark(fences=(300, 380, 400, 380, 300)),
        "2": {"fences_ft": [300, 380]},
    }
    assert league_mean_fences(parks) == [300.0, 380.0, 400.0, 380.0, 300.0]


def test_calc_33_is_positive_when_the_park_is_deeper_where_the_hitter_hits():
    """A pull-heavy left-handed hitter at a park with a deep right field."""
    parks = {
        VENUE: _ballpark(fences=(330, 385, 405, 385, 380)),
        OTHER_VENUE: _ballpark(fences=(330, 385, 405, 385, 330)),
    }
    pitches = [_air_ball(35.0, at_bat_number=i) for i in range(10)]
    result = calc_33_geometry_match(pitches, parks, VENUE)
    assert isinstance(result, DELTA)
    assert result.value.rate == pytest.approx(25.0)


def test_calc_33_is_negative_when_the_park_is_shallower_where_the_hitter_hits():
    parks = {
        VENUE: _ballpark(fences=(300, 385, 405, 385, 330)),
        OTHER_VENUE: _ballpark(fences=(340, 385, 405, 385, 330)),
    }
    pitches = [_air_ball(-35.0, at_bat_number=i) for i in range(10)]
    assert calc_33_geometry_match(pitches, parks, VENUE).value.rate == pytest.approx(
        -20.0
    )


def test_calc_33_weights_each_sector_by_how_often_the_hitter_uses_it():
    """Three balls to left, one to right, at a park 40 feet short in left and
    40 long in right: 0.75 * -20 + 0.25 * +20 = -10."""
    parks = {
        VENUE: _ballpark(fences=(300, 385, 405, 385, 360)),
        OTHER_VENUE: _ballpark(fences=(340, 385, 405, 385, 320)),
    }
    pitches = [_air_ball(-35.0, at_bat_number=i) for i in range(3)]
    pitches.append(_air_ball(35.0, at_bat_number=99))
    assert calc_33_geometry_match(pitches, parks, VENUE).value.rate == pytest.approx(
        -10.0
    )


def test_calc_33_denominator_is_the_tracked_air_ball_count():
    """Unlike most of this category, this really is a sample size."""
    pitches = [_air_ball(0.0, at_bat_number=i) for i in range(7)]
    assert calc_33_geometry_match(pitches, _parks(), VENUE).value.denominator == 7


def test_calc_33_is_none_without_a_tracked_air_ball():
    assert calc_33_geometry_match([], _parks(), VENUE) is None


def test_calc_33_is_none_for_a_venue_missing_from_the_ballpark_table():
    pitches = [_air_ball(0.0, at_bat_number=i) for i in range(3)]
    assert calc_33_geometry_match(pitches, _parks(), OTHER_VENUE) is None


# ---------------------------------------------------------------------------
# CALC_34 -- temperature
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "temperature,ordinal",
    [
        (35.0, -1.0),
        (59.9, -1.0),
        (60.0, 0.0),
        (74.9, 0.0),
        (75.0, 1.0),
        (89.9, 1.0),
        (90.0, 2.0),
        (104.0, 2.0),
    ],
)
def test_the_temperature_tiers_are_the_roadmaps_four_buckets(temperature, ordinal):
    assert temperature_tier(temperature) == ordinal


def test_calc_34_reports_degrees_against_the_neutral_midpoint():
    result = calc_34_temperature("89 degrees, Partly Cloudy.")
    assert result["CALC_34"].value.rate == pytest.approx(89.0 - NEUTRAL_TEMPERATURE_F)
    assert result["CALC_34_TIER"].value.rate == 1.0


def test_calc_34_is_signed_and_negative_on_a_cold_night():
    assert calc_34_temperature("45 degrees, Clear.")["CALC_34"].value.rate < 0


@pytest.mark.parametrize("key", ["CALC_34", "CALC_34_TIER"])
def test_calc_34_denominators_are_not_sample_sizes(key):
    """Both are 1, set only to satisfy the Rate shape. Issue #34's shrinkage must
    not read them as evidence weight, the same as CALC_64's rest days."""
    assert calc_34_temperature("80 degrees, Clear.")[key].value.denominator == 1


def test_calc_34_is_none_without_a_temperature():
    assert calc_34_temperature(None) == {"CALC_34": None, "CALC_34_TIER": None}


# ---------------------------------------------------------------------------
# CALC_35 -- air density
# ---------------------------------------------------------------------------


def test_air_density_at_the_isa_reference_reproduces_the_standard_value():
    """59F is the ISA sea-level temperature, so the density must come back to the
    constant the index divides by."""
    assert air_density(0.0, 59.0) == pytest.approx(STANDARD_AIR_DENSITY, rel=1e-4)


def test_thinner_air_at_altitude_raises_the_index():
    sea_level = calc_35_air_density_index(
        "75 degrees, Clear.", _parks(elevation=0), VENUE
    )
    coors = calc_35_air_density_index(
        "75 degrees, Clear.", _parks(elevation=5190), VENUE
    )
    assert coors.value.rate > sea_level.value.rate
    assert coors.value.rate == pytest.approx(1.20, abs=0.05)


def test_hotter_air_raises_the_index_at_a_fixed_elevation():
    cold = calc_35_air_density_index("40 degrees, Cloudy.", _parks(elevation=55), VENUE)
    hot = calc_35_air_density_index("95 degrees, Sunny.", _parks(elevation=55), VENUE)
    assert hot.value.rate > cold.value.rate


def test_a_cold_night_at_sea_level_falls_below_one():
    result = calc_35_air_density_index(
        "40 degrees, Cloudy.", _parks(elevation=20), VENUE
    )
    assert result.value.rate < 1.0


def test_calc_35_denominator_is_not_a_sample_size():
    result = calc_35_air_density_index("70 degrees, Clear.", _parks(), VENUE)
    assert result.value.denominator == 1


@pytest.mark.parametrize(
    "weather,parks",
    [(None, _parks()), ("Clear.", _parks()), ("70 degrees, Clear.", {})],
)
def test_calc_35_is_none_without_both_a_temperature_and_an_elevation(weather, parks):
    assert calc_35_air_density_index(weather, parks, VENUE) is None


# ---------------------------------------------------------------------------
# CALC_36 -- wind
# ---------------------------------------------------------------------------


def _pull_right():
    return [_air_ball(36.0, at_bat_number=i) for i in range(10)]


def test_a_wind_straight_out_to_the_hitters_own_sector_delivers_its_full_speed():
    result = calc_36_wind_effect("12 mph, Out To RF.", _pull_right())
    assert isinstance(result, DELTA)
    assert result.value.rate == pytest.approx(12.0)


def test_a_wind_blowing_in_from_the_hitters_own_sector_is_negative():
    assert calc_36_wind_effect(
        "12 mph, In From RF.", _pull_right()
    ).value.rate == pytest.approx(-12.0)


def test_a_wind_out_to_the_opposite_field_still_helps_but_less():
    """Fair territory spans 90 degrees, so the cosine never drops below 0.31."""
    own = calc_36_wind_effect("12 mph, Out To RF.", _pull_right()).value.rate
    opposite = calc_36_wind_effect("12 mph, Out To LF.", _pull_right()).value.rate
    assert 0 < opposite < own
    assert opposite == pytest.approx(12.0 * math.cos(math.radians(72.0)))


def test_a_pull_hitter_gains_more_from_a_wind_toward_his_pull_side():
    right = _pull_right()
    left = [_air_ball(-36.0, at_bat_number=i) for i in range(10)]
    wind = "12 mph, Out To RF."
    assert (
        calc_36_wind_effect(wind, right).value.rate
        > calc_36_wind_effect(wind, left).value.rate
    )


@pytest.mark.parametrize("wind", ["8 mph, L To R.", "8 mph, R To L.", "0 mph, None."])
def test_a_crossfield_or_calm_wind_is_zero_rather_than_missing(wind):
    """A real measurement: such a wind genuinely neither helps nor hurts carry."""
    result = calc_36_wind_effect(wind, _pull_right())
    assert result is not None
    assert result.value.rate == 0.0


def test_an_unrecognized_direction_is_none_rather_than_a_neutral_zero():
    """A vocabulary gap must never be mistaken for a calm day."""
    assert calc_36_wind_effect("9 mph, Sideways.", _pull_right()) is None


def test_calc_36_is_none_without_a_spray_distribution():
    assert calc_36_wind_effect("12 mph, Out To RF.", []) is None


def test_calc_36_denominator_is_the_tracked_air_ball_count():
    assert (
        calc_36_wind_effect("12 mph, Out To RF.", _pull_right()).value.denominator == 10
    )


# ---------------------------------------------------------------------------
# CALC_37 / CALC_38 -- situational splits
# ---------------------------------------------------------------------------


def _splits(**codes):
    return {
        code: {"hits": hits, "plateAppearances": pa}
        for code, (hits, pa) in codes.items()
    }


def test_calc_37_reads_the_condition_the_game_is_actually_played_in():
    batter = _splits(d=(18, 91), n=(35, 170))
    pitcher = _splits(d=(37, 150), n=(85, 290))
    day = calc_37_day_night(batter, pitcher, "day")
    night = calc_37_day_night(batter, pitcher, "night")
    assert day["CALC_37_BATTER"].value.rate == pytest.approx(18 / 91)
    assert night["CALC_37_BATTER"].value.rate == pytest.approx(35 / 170)
    assert night["CALC_37_PITCHER"].value.rate == pytest.approx(85 / 290)


def test_calc_37_divides_by_plate_appearances_not_at_bats():
    """Taking the API's `avg` would put an at-bat-denominated rate into a per-PA
    model, the CALC_57 units error in a new place."""
    batter = {"n": {"hits": 35, "atBats": 133, "plateAppearances": 170}}
    result = calc_37_day_night(batter, None, "night")
    assert result["CALC_37_BATTER"].value.denominator == 170


def test_calc_37_is_a_probability_on_both_sides():
    batter = _splits(n=(35, 170))
    pitcher = _splits(n=(85, 290))
    result = calc_37_day_night(batter, pitcher, "night")
    assert isinstance(result["CALC_37_BATTER"], PROBABILITY)
    assert isinstance(result["CALC_37_PITCHER"], PROBABILITY)


@pytest.mark.parametrize("day_night", [None, "", "twilight"])
def test_calc_37_refuses_to_guess_the_condition(day_night):
    batter = _splits(d=(18, 91), n=(35, 170))
    result = calc_37_day_night(batter, None, day_night)
    assert result == {"CALC_37_BATTER": None, "CALC_37_PITCHER": None}


@pytest.mark.parametrize("value", ["Day", "NIGHT", " night "])
def test_calc_37_accepts_the_schedules_own_casing(value):
    batter = _splits(d=(18, 91), n=(35, 170))
    assert calc_37_day_night(batter, None, value)["CALC_37_BATTER"] is not None


def test_calc_38_reads_the_pitcher_from_the_opposite_side_of_the_same_game():
    """When the hitter is home the opposing starter is away. Reading both from
    the same code would compare two home lines in a game with one home team."""
    batter = _splits(h=(26, 115), a=(27, 146))
    pitcher = _splits(h=(58, 206), a=(64, 234))
    result = calc_38_home_away(batter, pitcher, True)
    assert result["CALC_38_BATTER"].value.rate == pytest.approx(26 / 115)
    assert result["CALC_38_PITCHER"].value.rate == pytest.approx(64 / 234)


def test_calc_38_flips_both_sides_when_the_hitter_is_on_the_road():
    batter = _splits(h=(26, 115), a=(27, 146))
    pitcher = _splits(h=(58, 206), a=(64, 234))
    result = calc_38_home_away(batter, pitcher, False)
    assert result["CALC_38_BATTER"].value.rate == pytest.approx(27 / 146)
    assert result["CALC_38_PITCHER"].value.rate == pytest.approx(58 / 206)


def test_calc_38_refuses_to_guess_which_side_the_hitter_is_on():
    batter = _splits(h=(26, 115), a=(27, 146))
    assert calc_38_home_away(batter, None, None) == {
        "CALC_38_BATTER": None,
        "CALC_38_PITCHER": None,
    }


@pytest.mark.parametrize("splits", [None, {}, {"h": {}}, {"h": {"hits": 3}}])
def test_a_missing_or_empty_split_line_is_none(splits):
    assert calc_38_home_away(splits, None, True)["CALC_38_BATTER"] is None


# ---------------------------------------------------------------------------
# CALC_39 -- roof status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weather,roof,expected",
    [
        ("75 degrees, Clear.", "Open", "none"),
        ("70 degrees, Roof Closed.", "Retractable", "closed"),
        ("72 degrees, Dome.", "Dome", "closed"),
        ("70 degrees, Partly Cloudy.", "Retractable", "open"),
        ("70 degrees, Blizzard.", "Retractable", None),
        (None, "Retractable", None),
    ],
)
def test_the_roof_state_is_read_from_the_condition_and_the_park(
    weather, roof, expected
):
    assert roof_state(weather, roof) == expected


def test_an_open_air_park_reports_no_roof_whatever_the_weather():
    """An open-air park has no roof to be in an unknown state."""
    assert roof_state("70 degrees, Blizzard.", "Open") == "none"


def test_open_roof_index_inverts_the_blend():
    """A = f*C + (1-f)*O with f = 0.5, C = 90, A = 95 solves to O = 100."""
    assert open_roof_index(95.0, 40000, 90.0, 20000) == pytest.approx(100.0)


def test_open_roof_index_refuses_an_open_share_too_thin_to_invert():
    """Half a point of rounding error on an integer index divided by a 0.4
    percent open share is 135 index points, which is not a number."""
    share = MIN_OPEN_ROOF_SHARE / 2
    assert open_roof_index(99.0, 50000, 99.0, int(50000 * (1 - share))) is None


def test_calc_39_is_exactly_one_at_an_open_air_park():
    """Not an invented neutral: a park with no roof has one condition, so there
    is no adjustment to make."""
    result = calc_39_roof_status(
        _factors(), _parks(roof="Open"), VENUE, "75 degrees, Clear."
    )
    assert result.value.rate == 1.0


def test_calc_39_is_exactly_one_at_a_fixed_dome():
    """Every plate appearance there was under the roof, so the park's own factor
    already describes today. Tropicana's closed and all samples are both 31,614."""
    factors = {VENUE: _park_factor(all_index=97, closed=(97, 31614), n_pa=31614)}
    result = calc_39_roof_status(
        factors, _parks(roof="Dome"), VENUE, "72 degrees, Dome."
    )
    assert result.value.rate == 1.0


def test_calc_39_compares_closed_against_the_solved_open_index_not_the_blend():
    """The all-conditions index already contains the closed games, so dividing by
    it attenuates the effect. With C=93, A=95 and a 52 percent closed share the
    attenuated ratio is .979 and the real one is .957."""
    factors = {VENUE: _park_factor(all_index=95, closed=(93, 26000), n_pa=50000)}
    result = calc_39_roof_status(
        factors, _parks(roof="Retractable"), VENUE, "70 degrees, Roof Closed."
    )
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(0.957, abs=0.002)
    assert result.value.rate < 93 / 95


def test_calc_39_is_none_when_a_retractable_roof_is_open():
    """Savant publishes no working roof-open grouping, so there is no number."""
    factors = {VENUE: _park_factor(all_index=95, closed=(93, 26000))}
    assert (
        calc_39_roof_status(
            factors, _parks(roof="Retractable"), VENUE, "70 degrees, Partly Cloudy."
        )
        is None
    )


def test_calc_39_is_none_when_the_open_share_is_too_thin_to_invert():
    factors = {VENUE: _park_factor(all_index=99, closed=(99, 49800), n_pa=50000)}
    assert (
        calc_39_roof_status(
            factors, _parks(roof="Retractable"), VENUE, "70 degrees, Roof Closed."
        )
        is None
    )


def test_calc_39_is_none_when_the_roof_state_cannot_be_determined():
    factors = {VENUE: _park_factor(closed=(93, 26000))}
    assert calc_39_roof_status(factors, _parks(roof="Retractable"), VENUE, None) is None


# ---------------------------------------------------------------------------
# CALC_40 -- venue familiarity
# ---------------------------------------------------------------------------


def test_calc_40_counts_only_plate_appearances_at_this_venue():
    pitches = [
        _env_pitch(at_bat_number=1, events="single", home_team="NYY"),
        _env_pitch(at_bat_number=2, events="field_out", home_team="NYY"),
        _env_pitch(at_bat_number=3, events="single", home_team="BOS"),
    ]
    result = calc_40_venue_familiarity(pitches, "NYY")
    assert isinstance(result, PROBABILITY)
    assert result.value == (0.5, 2)


def test_calc_40_reduces_a_multi_pitch_plate_appearance_to_one():
    pitches = [
        _env_pitch(at_bat_number=1, pitch_number=1, description="ball"),
        _env_pitch(at_bat_number=1, pitch_number=2, events="single"),
    ]
    assert calc_40_venue_familiarity(pitches, "NYY").value.denominator == 1


def test_calc_40_excludes_spring_training():
    pitches = [
        _env_pitch(at_bat_number=1, events="single"),
        _env_pitch(at_bat_number=2, events="single", game_type="S"),
    ]
    assert calc_40_venue_familiarity(pitches, "NYY").value.denominator == 1


def test_calc_40_is_none_at_a_venue_with_no_history():
    pitches = [_env_pitch(at_bat_number=1, events="single", home_team="NYY")]
    assert calc_40_venue_familiarity(pitches, "SEA") is None


@pytest.mark.parametrize("home_team", [None, ""])
def test_calc_40_is_none_without_a_venue(home_team):
    pitches = [_env_pitch(at_bat_number=1, events="single")]
    assert calc_40_venue_familiarity(pitches, home_team) is None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


EXPECTED_KEYS = {
    "CALC_31",
    "CALC_32",
    "CALC_33",
    "CALC_34",
    "CALC_34_TIER",
    "CALC_35",
    "CALC_36",
    "CALC_37_BATTER",
    "CALC_37_PITCHER",
    "CALC_38_BATTER",
    "CALC_38_PITCHER",
    "CALC_39",
    "CALC_40",
}


def test_absent_input_resolves_every_key_to_none_rather_than_omitting_it():
    result = compute_category_05()
    assert set(result) == EXPECTED_KEYS
    assert all(value is None for value in result.values())


def test_the_key_set_does_not_depend_on_what_was_fetched():
    pitches = [_air_ball(0.0, at_bat_number=i) for i in range(5)]
    environment = {
        "venue_id": VENUE,
        "weather": "89 degrees, Partly Cloudy.",
        "wind": "9 mph, Out To RF.",
        "day_night": "night",
        "home_team": "NYY",
        "batter_is_home": True,
    }
    result = compute_category_05(
        environment,
        _parks(),
        _factors(),
        pitches,
        _splits(h=(26, 115), a=(27, 146), d=(18, 91), n=(35, 170)),
        _splits(h=(58, 206), a=(64, 234), d=(37, 150), n=(85, 290)),
        "R",
    )
    assert set(result) == EXPECTED_KEYS
    assert result["CALC_31"] is not None
    assert result["CALC_40"] is not None


def test_a_missing_venue_id_still_yields_the_weather_and_split_calculators():
    """venue_id gates the five venue-keyed calculators and nothing else, so a
    game whose venue is unknown still reports temperature and splits."""
    environment = {
        "venue_id": None,
        "weather": "89 degrees, Partly Cloudy.",
        "wind": None,
        "day_night": "night",
        "home_team": None,
        "batter_is_home": None,
    }
    result = compute_category_05(
        environment, _parks(), _factors(), [], _splits(n=(35, 170)), None, "R"
    )
    assert result["CALC_31"] is None
    assert result["CALC_34"] is not None
    assert result["CALC_37_BATTER"] is not None


@pytest.mark.parametrize("sector", SECTOR_NAMES)
def test_every_sector_name_is_reachable_from_some_fair_angle(sector):
    """Guards the sector table against a bound typo that would orphan a fence."""
    angles = [angle / 2 for angle in range(-90, 91)]
    assert any(sector_for_angle(angle) == sector for angle in angles)
