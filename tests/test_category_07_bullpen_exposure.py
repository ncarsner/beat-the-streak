"""Tests for calculators/category_07_bullpen_exposure.py."""

from datetime import date

import pytest

from calculators.category_07_bullpen_exposure import (
    LEVERAGE_CORE_SIZE,
    OPENER_MAX_BF_PER_START,
    RELIEF_BF_SHARE_MIN,
    bullpen_pool,
    calc_47_bullpen_hit_rate,
    calc_48_bullpen_fatigue,
    calc_49_handedness_mix,
    calc_50_bullpen_contact,
    calc_51_opener_adjustment,
    compute_category_07,
    leverage_core,
    leverage_score,
    relief_lines,
    relief_share,
    start_lines,
)
from calculators.common import DELTA, MULTIPLIER, PROBABILITY
from tests.conftest import _pitch, _relief_line, _reliever

TODAY = date(2026, 8, 9)


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------


def test_a_start_is_not_a_relief_appearance():
    pitcher = _reliever(games=[_relief_line(started=True), _relief_line(started=False)])
    assert len(relief_lines(pitcher, TODAY)) == 1
    assert len(start_lines(pitcher, TODAY)) == 1


def test_spring_training_lines_are_dropped():
    """Issue #42 is open because Categories 1-4 skipped this filter. The game
    log carries `gameType`, so here it costs nothing."""
    pitcher = _reliever(
        games=[
            _relief_line(game_date="2026-03-01", game_type="S", bf=10),
            _relief_line(game_date="2026-08-01", game_type="R", bf=4),
        ]
    )
    assert [g["batters_faced"] for g in relief_lines(pitcher, TODAY)] == [4]


@pytest.mark.parametrize("game_type", ["S", "E", "A"])
def test_every_non_competitive_game_type_is_dropped(game_type):
    pitcher = _reliever(games=[_relief_line(game_type=game_type)])
    assert relief_lines(pitcher, TODAY) == []


def test_todays_game_is_excluded():
    """The pool is resolved before first pitch, so today's line cannot exist
    yet, and a backtest anchored at a past date must not see it either."""
    pitcher = _reliever(
        games=[
            _relief_line(game_date="2026-08-09"),
            _relief_line(game_date="2026-08-08"),
        ]
    )
    assert [g["game_date"] for g in relief_lines(pitcher, TODAY)] == ["2026-08-08"]


def test_games_after_the_anchor_date_are_excluded():
    pitcher = _reliever(
        games=[
            _relief_line(game_date="2026-08-20"),
            _relief_line(game_date="2026-08-01"),
        ]
    )
    assert [g["game_date"] for g in relief_lines(pitcher, TODAY)] == ["2026-08-01"]


def test_relief_share_weighs_batters_faced_not_games():
    """A starter's one outing outweighs several relief appearances, which is the
    whole reason the share is not counted in games."""
    pitcher = _reliever(
        games=[_relief_line(started=True, bf=24)]
        + [_relief_line(bf=4) for _ in range(3)]
    )
    assert relief_share(pitcher, TODAY) == pytest.approx(12 / 36)


def test_relief_share_is_none_for_a_pitcher_who_has_faced_nobody():
    assert relief_share(_reliever(), TODAY) is None


@pytest.mark.parametrize(
    "relief_bf, start_bf, expected",
    [
        (100, 0, True),  # pure reliever
        (0, 100, False),  # pure starter
        (60, 40, True),  # swingman, mostly relief
        (40, 60, False),  # swingman, mostly starting
        (50, 50, True),  # exactly on the boundary, inclusive
    ],
)
def test_the_pool_boundary_sorts_swingmen(relief_bf, start_bf, expected):
    games = []
    if relief_bf:
        games.append(_relief_line(bf=relief_bf))
    if start_bf:
        games.append(_relief_line(started=True, bf=start_bf))
    pool = bullpen_pool([_reliever(games=games)], TODAY)
    assert bool(pool) is expected


def test_the_boundary_is_inclusive():
    assert RELIEF_BF_SHARE_MIN == 0.5


def test_a_pitcher_with_no_appearances_is_kept():
    """A fresh callup with no lines. Keeping him costs the appearance-weighted
    calculators nothing and keeps a real arm in the handedness mix."""
    assert len(bullpen_pool([_reliever()], TODAY)) == 1


# ---------------------------------------------------------------------------
# CALC_47
# ---------------------------------------------------------------------------


def test_calc_47_is_hits_per_batter_faced():
    pen = [_reliever(games=[_relief_line(bf=10, hits=3, walks=2)])]
    assert calc_47_bullpen_hit_rate(pen, TODAY)["CALC_47"] == PROBABILITY(
        pytest.approx((0.3, 10), rel=1e-9)
    )


def test_calc_47_ba_uses_at_bats_and_so_reads_higher():
    """A walk is a plate appearance that is not an at bat, so BA runs above the
    per-PA rate. Blending the wrong one into a per-PA model is the CALC_57
    units error."""
    pen = [_reliever(games=[_relief_line(bf=10, hits=3, walks=2)])]
    result = calc_47_bullpen_hit_rate(pen, TODAY)
    assert result["CALC_47"].value.rate == pytest.approx(3 / 10)
    assert result["CALC_47_BA"].value.rate == pytest.approx(3 / 8)
    assert result["CALC_47_BA"].value.rate > result["CALC_47"].value.rate


def test_calc_47_sums_across_the_whole_pool():
    pen = [
        _reliever(1, games=[_relief_line(bf=10, hits=2)]),
        _reliever(2, games=[_relief_line(bf=10, hits=4)]),
    ]
    assert calc_47_bullpen_hit_rate(pen, TODAY)["CALC_47"].value == (0.3, 20)


def test_calc_47_ignores_starts_by_a_swingman():
    """The reason the aggregation is over appearances: a swingman's starting
    work must not drag the bullpen rate toward rotation quality."""
    pen = [
        _reliever(
            games=[
                _relief_line(bf=10, hits=2),
                _relief_line(started=True, bf=100, hits=40),
            ]
        )
    ]
    assert calc_47_bullpen_hit_rate(pen, TODAY)["CALC_47"].value == (0.2, 10)


@pytest.mark.parametrize("key", ["CALC_47", "CALC_47_BA"])
def test_calc_47_is_none_with_no_relief_work(key):
    assert calc_47_bullpen_hit_rate([_reliever()], TODAY)[key] is None


# ---------------------------------------------------------------------------
# CALC_48
# ---------------------------------------------------------------------------


def test_leverage_score_is_saves_plus_holds_per_appearance():
    pitcher = _reliever(
        games=[
            _relief_line(saves=1),
            _relief_line(holds=1),
            _relief_line(),
            _relief_line(),
        ]
    )
    assert leverage_score(pitcher, TODAY) == pytest.approx(0.5)


def test_leverage_score_is_none_without_relief_work():
    assert leverage_score(_reliever(), TODAY) is None


def test_the_core_takes_the_most_trusted_arms():
    pen = [
        _reliever(1, games=[_relief_line(saves=1)]),
        _reliever(2, games=[_relief_line(holds=1), _relief_line()]),
        _reliever(3, games=[_relief_line()]),
    ]
    assert [p["id"] for p in leverage_core(pen, TODAY)] == [1, 2]


def test_a_mop_up_arm_never_joins_the_core():
    """Padding a short core out with pitchers who have never held a lead would
    report the wrong group as depleted."""
    pen = [_reliever(i, games=[_relief_line()]) for i in range(1, 6)]
    assert leverage_core(pen, TODAY) == []


def test_the_core_is_capped():
    pen = [_reliever(i, games=[_relief_line(saves=1)]) for i in range(1, 8)]
    assert len(leverage_core(pen, TODAY)) == LEVERAGE_CORE_SIZE


def test_core_ties_break_deterministically():
    pen = [_reliever(i, games=[_relief_line(saves=1)]) for i in (9, 3, 7, 1)]
    assert [p["id"] for p in leverage_core(pen, TODAY)] == [1, 3, 7]


def test_calc_48_averages_recent_pitches_over_the_core():
    pen = [
        _reliever(1, games=[_relief_line(game_date="2026-08-08", pitches=20, saves=1)]),
        _reliever(2, games=[_relief_line(game_date="2026-08-07", pitches=10, holds=1)]),
    ]
    assert calc_48_bullpen_fatigue(pen, TODAY)["CALC_48"] == DELTA((15.0, 2))


def test_calc_48_windows_end_yesterday_and_differ_in_length():
    pen = [
        _reliever(
            1,
            games=[
                _relief_line(game_date="2026-08-08", pitches=20, saves=1),
                _relief_line(game_date="2026-08-04", pitches=30),
            ],
        )
    ]
    result = calc_48_bullpen_fatigue(pen, TODAY)
    assert result["CALC_48"].value.rate == 20.0
    assert result["CALC_48_7D"].value.rate == 50.0


def test_a_rested_core_reads_zero_load_rather_than_none():
    """An empty window normally means no evidence. Here it means the arm
    demonstrably did not pitch, which is the reading the calculator exists for."""
    pen = [_reliever(1, games=[_relief_line(game_date="2026-06-01", saves=1)])]
    result = calc_48_bullpen_fatigue(pen, TODAY)
    assert result["CALC_48"].value.rate == 0.0
    assert result["CALC_48_AVAILABLE"].value.rate == 1.0


def test_calc_48_available_counts_arms_idle_for_two_days():
    pen = [
        _reliever(1, games=[_relief_line(game_date="2026-08-08", saves=1)]),
        _reliever(2, games=[_relief_line(game_date="2026-08-01", holds=1)]),
    ]
    assert calc_48_bullpen_fatigue(pen, TODAY)["CALC_48_AVAILABLE"].value == (0.5, 2)


@pytest.mark.parametrize("key", ["CALC_48", "CALC_48_7D", "CALC_48_AVAILABLE"])
def test_calc_48_is_none_without_a_core(key):
    assert calc_48_bullpen_fatigue([_reliever()], TODAY)[key] is None


def test_calc_48_load_keys_are_delta_and_availability_is_a_multiplier():
    """Pitch counts are units, not rates, so they follow the CALC_60 and
    CALC_62 precedent rather than being normalized against a league constant
    that issue #38 blocks."""
    result = calc_48_bullpen_fatigue(
        [_reliever(1, games=[_relief_line(saves=1)])], TODAY
    )
    assert isinstance(result["CALC_48"], DELTA)
    assert isinstance(result["CALC_48_7D"], DELTA)
    assert isinstance(result["CALC_48_AVAILABLE"], MULTIPLIER)


def test_a_start_does_not_count_toward_bullpen_fatigue():
    pen = [
        _reliever(
            1,
            games=[
                _relief_line(game_date="2026-08-08", pitches=10, saves=1),
                _relief_line(game_date="2026-08-08", pitches=95, started=True),
            ],
        )
    ]
    assert calc_48_bullpen_fatigue(pen, TODAY)["CALC_48"].value.rate == 10.0


# ---------------------------------------------------------------------------
# CALC_49
# ---------------------------------------------------------------------------


def test_calc_49_weights_by_batters_faced():
    pen = [
        _reliever(1, hand="L", games=[_relief_line(bf=90)]),
        _reliever(2, hand="R", games=[_relief_line(bf=10)]),
    ]
    result = calc_49_handedness_mix(pen, "L", TODAY)
    assert result["CALC_49"].value == (0.9, 100)
    assert result["CALC_49_LHP"].value == (0.9, 100)


def test_calc_49_measures_the_hitters_bad_side():
    pen = [
        _reliever(1, hand="L", games=[_relief_line(bf=30)]),
        _reliever(2, hand="R", games=[_relief_line(bf=70)]),
    ]
    assert calc_49_handedness_mix(pen, "R", TODAY)["CALC_49"].value.rate == 0.7
    assert calc_49_handedness_mix(pen, "L", TODAY)["CALC_49"].value.rate == 0.3


def test_a_switch_hitter_is_never_at_a_platoon_disadvantage():
    """He picks his side after the pitcher is announced, so against a bullpen he
    has the edge on every arm in it. That is 0.0 with the full sample behind it,
    not None."""
    pen = [
        _reliever(1, hand="L", games=[_relief_line(bf=40)]),
        _reliever(2, hand="R", games=[_relief_line(bf=60)]),
    ]
    result = calc_49_handedness_mix(pen, "S", TODAY)
    assert result["CALC_49"].value == (0.0, 100)


def test_calc_49_lhp_share_survives_an_unknown_batter():
    pen = [
        _reliever(1, hand="L", games=[_relief_line(bf=25)]),
        _reliever(2, hand="R", games=[_relief_line(bf=75)]),
    ]
    result = calc_49_handedness_mix(pen, None, TODAY)
    assert result["CALC_49"] is None
    assert result["CALC_49_LHP"].value == (0.25, 100)


def test_a_pitcher_with_an_unresolved_hand_leaves_both_sides_of_the_rate():
    pen = [
        _reliever(1, hand="L", games=[_relief_line(bf=50)]),
        _reliever(2, hand=None, games=[_relief_line(bf=50)]),
    ]
    result = calc_49_handedness_mix(pen, "L", TODAY)
    assert result["CALC_49"].value == (1.0, 50)


def test_calc_49_ignores_relief_work_by_a_pitcher_with_none():
    pen = [_reliever(1, hand="L"), _reliever(2, hand="R", games=[_relief_line(bf=20)])]
    assert calc_49_handedness_mix(pen, "R", TODAY)["CALC_49"].value == (1.0, 20)


@pytest.mark.parametrize("key", ["CALC_49", "CALC_49_LHP"])
def test_calc_49_is_none_with_no_relief_work(key):
    assert calc_49_handedness_mix([_reliever()], "R", TODAY)[key] is None


# ---------------------------------------------------------------------------
# CALC_50
# ---------------------------------------------------------------------------


def test_calc_50_is_balls_in_play_per_batter_faced():
    pen = [
        _reliever(games=[_relief_line(bf=100, strike_outs=25, walks=8, hit_by_pitch=2)])
    ]
    assert calc_50_bullpen_contact(pen, TODAY)["CALC_50"].value == (0.65, 100)


def test_calc_50_whiff_needs_pitch_data():
    pen = [_reliever(games=[_relief_line(bf=20)])]
    assert calc_50_bullpen_contact(pen, TODAY)["CALC_50_WHIFF"] is None


def test_calc_50_whiff_is_misses_over_swings():
    pitches = [
        _pitch(description="swinging_strike"),
        _pitch(description="foul"),
        _pitch(description="hit_into_play"),
        _pitch(description="ball"),
    ]
    result = calc_50_bullpen_contact([_reliever()], TODAY, pitches)
    assert result["CALC_50_WHIFF"].value == (pytest.approx(1 / 3), 3)


def test_calc_50_in_play_resolves_without_pitch_data():
    """The two keys come from different sources, and the cheap one must not
    depend on the expensive one having been fetched."""
    pen = [_reliever(games=[_relief_line(bf=10, strike_outs=2, walks=1)])]
    result = calc_50_bullpen_contact(pen, TODAY)
    assert result["CALC_50"] is not None
    assert result["CALC_50_WHIFF"] is None


def test_calc_50_keys_are_multipliers():
    pen = [_reliever(games=[_relief_line(bf=10)])]
    assert isinstance(calc_50_bullpen_contact(pen, TODAY)["CALC_50"], MULTIPLIER)


# ---------------------------------------------------------------------------
# CALC_51
# ---------------------------------------------------------------------------


def test_a_short_average_start_reads_as_an_opener():
    starter = _reliever(games=[_relief_line(started=True, bf=5) for _ in range(4)])
    result = calc_51_opener_adjustment(starter, TODAY)
    assert result["CALC_51"].value == (1.0, 1)
    assert result["CALC_51_BF_PER_START"].value == (5.0, 4)


def test_a_conventional_starter_does_not():
    starter = _reliever(games=[_relief_line(started=True, bf=24) for _ in range(5)])
    assert calc_51_opener_adjustment(starter, TODAY)["CALC_51"].value.rate == 0.0


def test_the_opener_boundary_is_inclusive():
    starter = _reliever(
        games=[_relief_line(started=True, bf=int(OPENER_MAX_BF_PER_START))]
    )
    assert calc_51_opener_adjustment(starter, TODAY)["CALC_51"].value.rate == 1.0


def test_relief_appearances_do_not_dilute_the_start_length():
    """An opener has many relief outings and few starts, and averaging them
    together would make every reliever look like an opener."""
    starter = _reliever(
        games=[_relief_line(started=True, bf=5)]
        + [_relief_line(bf=4) for _ in range(40)]
    )
    assert calc_51_opener_adjustment(starter, TODAY)["CALC_51_BF_PER_START"].value == (
        5.0,
        1,
    )


@pytest.mark.parametrize("key", ["CALC_51", "CALC_51_BF_PER_START"])
def test_calc_51_is_none_for_a_pitcher_who_has_never_started(key):
    starter = _reliever(games=[_relief_line() for _ in range(10)])
    assert calc_51_opener_adjustment(starter, TODAY)[key] is None


@pytest.mark.parametrize("key", ["CALC_51", "CALC_51_BF_PER_START"])
def test_calc_51_is_none_without_a_starter(key):
    assert calc_51_opener_adjustment(None, TODAY)[key] is None


def test_calc_51_indicator_denominator_is_not_a_sample_size():
    """Issue #34 must not read it as evidence weight; it is a placeholder."""
    starter = _reliever(games=[_relief_line(started=True, bf=5) for _ in range(9)])
    assert calc_51_opener_adjustment(starter, TODAY)["CALC_51"].value.denominator == 1


# ---------------------------------------------------------------------------
# compute_category_07
# ---------------------------------------------------------------------------


CATEGORY_07_KEYS = {
    "CALC_47",
    "CALC_47_BA",
    "CALC_48",
    "CALC_48_7D",
    "CALC_48_AVAILABLE",
    "CALC_49",
    "CALC_49_LHP",
    "CALC_50",
    "CALC_50_WHIFF",
    "CALC_51",
    "CALC_51_BF_PER_START",
}


def test_every_key_is_present_with_no_input():
    assert set(compute_category_07()) == CATEGORY_07_KEYS


def test_absent_input_resolves_every_key_to_none():
    assert set(compute_category_07().values()) == {None}


def test_the_aggregate_narrows_the_pool_itself():
    """A caller passing a whole roster must not get rotation innings counted."""
    roster = [
        _reliever(1, games=[_relief_line(bf=20, hits=4)]),
        _reliever(2, games=[_relief_line(started=True, bf=200, hits=80)]),
    ]
    assert compute_category_07(roster, today=TODAY)["CALC_47"].value == (0.2, 20)


def test_the_starter_is_read_separately_from_the_pool():
    roster = [_reliever(1, games=[_relief_line(bf=20)])]
    starter = _reliever(2, games=[_relief_line(started=True, bf=6)])
    result = compute_category_07(roster, starter=starter, today=TODAY)
    assert result["CALC_51"].value.rate == 1.0


def test_the_aggregate_threads_the_anchor_date():
    """Every recency window has to be evaluable against a past date, or issue
    #35 cannot backtest the category."""
    roster = [
        _reliever(1, games=[_relief_line(game_date="2026-08-08", pitches=40, saves=1)])
    ]
    early = compute_category_07(roster, today=date(2026, 7, 1))
    late = compute_category_07(roster, today=TODAY)
    assert early["CALC_48"] is None
    assert late["CALC_48"].value.rate == 40.0
