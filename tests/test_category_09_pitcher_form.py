"""Tests for the Category 9 pitcher-form calculators."""

from datetime import date

import pytest

from calculators.common import DELTA, MULTIPLIER, PROBABILITY
from calculators.category_09_pitcher_form import (
    GAME_SCORE_V2_BASE,
    _game_runs,
    MEATBALL_ZONE,
    REST_TIER_LONG,
    REST_TIER_NORMAL,
    REST_TIER_SHORT,
    calc_60_recent_game_score,
    calc_61_recent_hit_allowance,
    calc_62_velocity_delta,
    calc_63_command_delta,
    calc_64_rest_days,
    calc_65_recent_hard_hit_allowed,
    compute_category_09,
    game_score_v2,
    rest_tier,
    starts,
)
from tests.conftest import _form_pitch_thrown, _start

TODAY = date(2026, 7, 15)


# ---------------------------------------------------------------------------
# Start reconstruction
# ---------------------------------------------------------------------------


def test_a_first_inning_zero_out_appearance_is_a_start():
    assert len(starts(_start(innings=2))) == 1


def test_a_relief_appearance_is_dropped():
    """Every Category 9 calculator is about the starter a hitter will face, and a
    reliever's line answers a different question."""
    relief = _start(innings=1)
    for pitch in relief:
        pitch["inning"] = 7
    assert starts(relief) == []


def test_an_appearance_entering_mid_inning_is_dropped():
    mid = _start(innings=1)
    for pitch in mid:
        pitch["outs_when_up"] = pitch["outs_when_up"] + 1
    assert starts(mid) == []


@pytest.mark.parametrize("game_type", ["S", "E", "A"])
def test_non_competitive_starts_are_dropped(game_type):
    assert starts(_start(innings=2, game_type=game_type)) == []


def test_postseason_starts_are_kept():
    for code in ("F", "D", "L", "W"):
        assert len(starts(_start(innings=1, game_type=code))) == 1


def test_starts_are_ordered_oldest_first():
    pitches = (
        _start(game_pk=3, game_date="2026-07-10")
        + _start(game_pk=1, game_date="2026-07-01")
        + _start(game_pk=2, game_date="2026-07-05")
    )
    assert [s["game_pk"] for s in starts(pitches)] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Innings pitched
# ---------------------------------------------------------------------------


def test_a_clean_outing_counts_three_outs_per_inning():
    assert starts(_start(innings=6))[0]["outs"] == 18


def test_a_completed_inning_uses_game_state_not_the_event_map():
    """The rule that matters. A baserunner retired on a batted ball is an out the
    batter's event does not name, which cost 1 out in 2 of 104 probe half-innings.
    Every half-inning but the pitcher's last must have ended with him on the
    mound, so it contributed exactly 3 minus the outs he entered with."""
    pitches = _start(
        innings=2,
        events_per_inning=[
            # Two named outs, but the inning ended: a runner was retired on the
            # single without the batter's event saying so.
            ["field_out", "single", "field_out"],
            ["field_out", "field_out", "field_out"],
        ],
    )
    assert starts(pitches)[0]["outs"] == 6


def test_the_final_inning_falls_back_to_the_event_map():
    """Pulled with two outs in the second: game state cannot settle the count, so
    the event map is used and innings pitched becomes a lower bound."""
    pitches = _start(
        innings=2,
        events_per_inning=[
            ["field_out", "field_out", "field_out"],
            ["strikeout", "strikeout", "single"],
        ],
    )
    assert starts(pitches)[0]["outs"] == 5


def test_a_double_play_records_two_outs():
    pitches = _start(
        innings=1, events_per_inning=[["field_out", "grounded_into_double_play"]]
    )
    assert starts(pitches)[0]["outs"] == 3


def test_runs_are_measured_across_all_pitch_rows_not_terminal_ones():
    """A run can score on a non-terminal pitch (wild pitch, balk, steal of home),
    which cost one run in 1 of the probe frame's 19 starts."""
    pitches = _start(
        innings=1, events_per_inning=[["field_out", "field_out", "field_out"]]
    )
    # A wild pitch mid-plate-appearance scores a run that no terminal event names.
    pitches.insert(
        1,
        _form_pitch_thrown(
            at_bat_number=1,
            pitch_number=2,
            description="ball",
            bat_score=0,
            post_bat_score=1,
        ),
    )
    for p in pitches[2:]:
        p["bat_score"] = p["post_bat_score"] = 1
    assert starts(pitches)[0]["runs"] == 1


def test_runs_are_scoped_to_one_game_not_one_inning_number():
    """Grouping on `inning` alone would collapse every game's first inning into
    one bucket and return the season-wide score spread: a large, plausible,
    entirely wrong number. The only caller passes one game, but correctness that
    does not depend on the caller costs one tuple."""
    rows = [
        _form_pitch_thrown(game_pk=1, inning=1, bat_score=0, post_bat_score=1),
        _form_pitch_thrown(game_pk=2, inning=1, bat_score=8, post_bat_score=9),
    ]
    assert _game_runs(rows) == 2


# ---------------------------------------------------------------------------
# CALC_60 -- Game Score v2
# ---------------------------------------------------------------------------


def test_game_score_v2_matches_the_published_formula():
    start = {
        "outs": 18,
        "strikeouts": 7,
        "walks": 2,
        "hits": 5,
        "runs": 2,
        "home_runs": 1,
    }
    expected = GAME_SCORE_V2_BASE + 2 * 18 + 7 - 2 * 2 - 2 * 5 - 3 * 2 - 6 * 1
    assert game_score_v2(start) == expected


def test_a_perfect_six_innings_scores_well_above_the_base():
    start = dict(outs=18, strikeouts=6, walks=0, hits=0, runs=0, home_runs=0)
    assert game_score_v2(start) == GAME_SCORE_V2_BASE + 42


def test_calc_60_averages_the_last_two_starts():
    pitches = (
        _start(game_pk=1, game_date="2026-07-01", innings=6)
        + _start(game_pk=2, game_date="2026-07-06", innings=6)
        + _start(game_pk=3, game_date="2026-07-11", innings=2)
    )
    result = calc_60_recent_game_score(pitches, starts_back=2)
    assert isinstance(result, DELTA)
    assert result.value.denominator == 2
    recent = starts(pitches)[-2:]
    assert result.value.rate == pytest.approx(sum(game_score_v2(s) for s in recent) / 2)


def test_calc_60_returns_none_without_a_start():
    assert calc_60_recent_game_score([]) is None


# ---------------------------------------------------------------------------
# CALC_61
# ---------------------------------------------------------------------------


def test_calc_61_computes_hits_per_nine_and_whip_over_innings_pitched():
    pitches = _start(
        innings=3,
        events_per_inning=[
            ["single", "walk", "field_out", "field_out", "field_out"],
            ["field_out", "field_out", "field_out"],
            ["single", "field_out", "field_out", "field_out"],
        ],
    )
    result = calc_61_recent_hit_allowance(pitches, starts_back=3)
    # 9 outs = 3.0 IP, 2 hits, 1 walk
    assert result["CALC_61_HITS_PER_9"].value.rate == pytest.approx(6.0)
    assert result["CALC_61_WHIP"].value.rate == pytest.approx(1.0)
    assert result["CALC_61_HITS_PER_9"].value.denominator == 9


def test_calc_61_is_multiplier_not_probability():
    """A WHIP of 1.2 and a hits-per-nine of 8.5 are neither probabilities nor on
    the p_hit scale."""
    result = calc_61_recent_hit_allowance(_start(innings=3))
    assert isinstance(result["CALC_61_WHIP"], MULTIPLIER)
    assert isinstance(result["CALC_61_HITS_PER_9"], MULTIPLIER)


def test_calc_61_returns_none_without_a_start():
    result = calc_61_recent_hit_allowance([])
    assert result == {"CALC_61_HITS_PER_9": None, "CALC_61_WHIP": None}


# ---------------------------------------------------------------------------
# CALC_62
# ---------------------------------------------------------------------------


def test_calc_62_is_negative_when_the_last_start_lost_velocity():
    """Negative is the interesting direction: the ROADMAP calls out a loss of 1.5
    mph or more as a hit boost, so a consumer reading magnitude alone would treat
    a velocity spike as a red flag."""
    pitches = (
        _start(game_pk=1, game_date="2026-07-01", release_speed=96.0)
        + _start(game_pk=2, game_date="2026-07-06", release_speed=96.0)
        + _start(game_pk=3, game_date="2026-07-11", release_speed=90.0)
    )
    result = calc_62_velocity_delta(pitches)
    assert isinstance(result, DELTA)
    assert result.value.rate < 0
    assert result.value.rate == pytest.approx(90.0 - 94.0)


def test_calc_62_measures_fastballs_only():
    """A mean over the whole arsenal tracks pitch selection rather than arm
    strength, so a start with more breaking balls would read as lost velocity."""
    pitches = _start(game_pk=1, game_date="2026-07-01", release_speed=95.0)
    slow_breaking = _start(
        game_pk=2, game_date="2026-07-06", pitch_type="CU", release_speed=78.0
    )
    result = calc_62_velocity_delta(pitches + slow_breaking)
    assert result is None  # the last start threw no fastball at all


def test_calc_62_returns_none_when_no_fastball_was_thrown():
    assert calc_62_velocity_delta(_start(pitch_type="CH")) is None


def test_calc_62_returns_none_without_a_start():
    assert calc_62_velocity_delta([]) is None


# ---------------------------------------------------------------------------
# CALC_63
# ---------------------------------------------------------------------------


def _walk_start(game_pk, game_date, walks, outs=3):
    events = ["walk"] * walks + ["field_out"] * outs
    return _start(game_pk=game_pk, game_date=game_date, events_per_inning=[events])


def test_calc_63_primary_is_the_walk_rate_delta():
    pitches = (
        _walk_start(1, "2026-07-01", walks=0)
        + _walk_start(2, "2026-07-06", walks=0)
        + _walk_start(3, "2026-07-11", walks=3)
    )
    result = calc_63_command_delta(pitches, starts_back=1)
    assert isinstance(result["CALC_63"], DELTA)
    # recent: 3 walks / 6 batters = .5; season: 3 / 12 = .25
    assert result["CALC_63"].value.rate == pytest.approx(0.25)
    assert result["CALC_63"].value.denominator == 6


def test_calc_63_zone_delta_is_negative_when_command_slips():
    """Negative is the ROADMAP's "zone percent drop"."""
    pitches = _start(game_pk=1, game_date="2026-07-01", zone=5) + _start(
        game_pk=2, game_date="2026-07-06", zone=13
    )
    result = calc_63_command_delta(pitches, starts_back=1)
    assert result["CALC_63_ZONE_DELTA"].value.rate == pytest.approx(-0.5)


def test_calc_63_meatball_counts_only_the_middle_zone():
    pitches = _start(
        game_pk=1, game_date="2026-07-01", events_per_inning=[["field_out"] * 4]
    )
    for pitch, zone in zip(pitches, [MEATBALL_ZONE, MEATBALL_ZONE, 1, 13]):
        pitch["zone"] = zone
    result = calc_63_command_delta(pitches, starts_back=1)
    assert isinstance(result["CALC_63_MEATBALL"], MULTIPLIER)
    assert result["CALC_63_MEATBALL"].value.rate == pytest.approx(0.5)


def test_calc_63_ignores_untracked_pitches_in_both_zone_terms():
    pitches = _start(
        game_pk=1, game_date="2026-07-01", events_per_inning=[["field_out"] * 3]
    )
    pitches[2]["zone"] = None
    result = calc_63_command_delta(pitches, starts_back=1)
    assert result["CALC_63_MEATBALL"].value.denominator == 2


def test_calc_63_returns_none_on_every_key_without_a_start():
    assert calc_63_command_delta([]) == {
        "CALC_63": None,
        "CALC_63_ZONE_DELTA": None,
        "CALC_63_MEATBALL": None,
    }


# ---------------------------------------------------------------------------
# CALC_64
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "days,tier",
    [
        (0, REST_TIER_SHORT),
        (3, REST_TIER_SHORT),
        (4, REST_TIER_NORMAL),
        (5, REST_TIER_NORMAL),
        (6, REST_TIER_LONG),
        (10, REST_TIER_LONG),
    ],
)
def test_rest_tier_follows_the_roadmap_split(days, tier):
    """3d short, 4-5d normal, 6+d long."""
    assert rest_tier(days) == tier


def test_rest_tier_returns_none_for_no_reading():
    assert rest_tier(None) is None


def test_calc_64_counts_days_from_the_last_start_to_today():
    pitches = _start(game_pk=1, game_date="2026-07-10")
    result = calc_64_rest_days(pitches, today=TODAY)
    assert isinstance(result, DELTA)
    assert result.value.rate == pytest.approx(5.0)


def test_calc_64_denominator_is_not_a_sample_size():
    """Rest is a single scalar read off one date. There is nothing to average and
    nothing to shrink, so the denominator is 1 and must not be read as evidence
    weight the way CALC_60's start count is."""
    result = calc_64_rest_days(_start(game_date="2026-07-10"), today=TODAY)
    assert result.value.denominator == 1


def test_calc_64_uses_the_most_recent_start():
    pitches = _start(game_pk=1, game_date="2026-06-01") + _start(
        game_pk=2, game_date="2026-07-11"
    )
    assert calc_64_rest_days(pitches, today=TODAY).value.rate == pytest.approx(4.0)


def test_calc_64_returns_none_without_a_start():
    assert calc_64_rest_days([], today=TODAY) is None


# ---------------------------------------------------------------------------
# CALC_65
# ---------------------------------------------------------------------------


def test_calc_65_is_hard_hit_over_measured_batted_balls():
    pitches = _start(
        game_pk=1, game_date="2026-07-01", events_per_inning=[["field_out"] * 4]
    )
    for pitch, speed in zip(pitches, [100.0, 99.0, 80.0, 70.0]):
        pitch["description"] = "hit_into_play"
        pitch["launch_speed"] = speed
    result = calc_65_recent_hard_hit_allowed(pitches, starts_back=1)
    assert isinstance(result, MULTIPLIER)
    assert result.value.rate == pytest.approx(0.5)
    assert result.value.denominator == 4


def test_calc_65_skips_batted_balls_with_no_exit_velocity():
    """A null reading is no measurement, never a soft-hit ball."""
    pitches = _start(
        game_pk=1, game_date="2026-07-01", events_per_inning=[["field_out"] * 2]
    )
    for pitch, speed in zip(pitches, [100.0, None]):
        pitch["description"] = "hit_into_play"
        pitch["launch_speed"] = speed
    result = calc_65_recent_hard_hit_allowed(pitches, starts_back=1)
    assert result.value.denominator == 1
    assert result.value.rate == pytest.approx(1.0)


def test_calc_65_returns_none_when_nothing_was_put_in_play():
    pitches = _start(
        game_pk=1, game_date="2026-07-01", events_per_inning=[["strikeout"] * 3]
    )
    assert calc_65_recent_hard_hit_allowed(pitches, starts_back=1) is None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


CATEGORY_09_KEYS = {
    "CALC_60",
    "CALC_61_HITS_PER_9",
    "CALC_61_WHIP",
    "CALC_62",
    "CALC_63",
    "CALC_63_ZONE_DELTA",
    "CALC_63_MEATBALL",
    "CALC_64",
    "CALC_65",
}


@pytest.mark.parametrize("pitches", [None, [], _start(innings=1)])
def test_compute_category_09_always_emits_the_full_key_set(pitches):
    assert set(compute_category_09(pitches, today=TODAY)) == CATEGORY_09_KEYS


def test_compute_category_09_resolves_every_key_to_none_without_input():
    assert set(compute_category_09().values()) == {None}


def test_compute_category_09_populates_every_key_on_a_full_season():
    pitches = []
    for i in range(4):
        start = _start(
            game_pk=i + 1,
            game_date=f"2026-07-{i * 3 + 1:02d}",
            release_speed=95.0 - i,
            events_per_inning=[
                ["single", "walk", "field_out", "field_out", "field_out"],
                ["field_out", "field_out", "field_out"],
            ],
        )
        for pitch in start:
            if pitch["events"] == "single":
                pitch["description"] = "hit_into_play"
                pitch["launch_speed"] = 101.0
        pitches += start
    result = compute_category_09(pitches, today=TODAY)
    assert set(result) == CATEGORY_09_KEYS
    assert all(v is not None for v in result.values()), {
        k: v for k, v in result.items() if v is None
    }


def test_every_category_09_value_is_role_tagged():
    result = compute_category_09(_start(innings=3), today=TODAY)
    for value in result.values():
        assert value is None or isinstance(value, (MULTIPLIER, DELTA))


def test_no_category_09_value_is_a_probability():
    """Nothing here is on the p_hit scale: the category reports pitcher
    diagnostics in game-score points, mph, days, WHIP, and shares."""
    result = compute_category_09(_start(innings=3), today=TODAY)
    assert not any(isinstance(v, PROBABILITY) for v in result.values())
