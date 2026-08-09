import pytest

from calculators import empty_line
from calculators.category_01_bvp_matchups import (
    calc_01_bvp_career_hit_rate,
    calc_02_bvp_season_hit_rate,
    calc_03_bvp_recent_window_hit_rate,
    calc_04_bvp_contact_rate,
    compute_category_01,
    parse_bvp_stats,
    calc_05_bvp_hard_hit_rate,
    calc_06_bvp_xba,
    calc_06_bvp_xwoba,
    calc_07_bvp_whiff_rate,
    calc_08_bvp_putaway_rate,
)
from tests.conftest import _split, _vsplayer_payload, _pitch


# ---- parse_bvp_stats ----


def test_parse_bvp_stats_splits_seasons_and_career():
    payload = _vsplayer_payload(
        [
            _split(season=2024, pa=10, ab=9, h=3, so=2, bb=1),
            _split(season=2026, pa=6, ab=6, h=2, so=0, bb=0),
        ],
        total=_split(pa=16, ab=15, h=5, so=2, bb=1),
    )
    bvp = parse_bvp_stats(payload)
    assert bvp["career"]["plateAppearances"] == 16
    assert sorted(bvp["by_season"]) == [2024, 2026]
    assert bvp["by_season"][2026]["hits"] == 2


def test_parse_bvp_stats_prefers_season_splits_over_a_disagreeing_api_total():
    """Observed live: vsPlayerTotal reported 2 PA against a 3 PA season split."""
    payload = _vsplayer_payload(
        [_split(season=2026, pa=3, ab=3, h=0)], total=_split(pa=2, ab=2, h=0)
    )
    assert parse_bvp_stats(payload)["career"]["plateAppearances"] == 3


def test_parse_bvp_stats_falls_back_to_api_total_without_season_splits():
    payload = _vsplayer_payload([], total=_split(pa=9, ab=8, h=3, so=1, bb=1))
    bvp = parse_bvp_stats(payload)
    assert bvp["by_season"] == {}
    assert bvp["career"]["plateAppearances"] == 9


def test_parse_bvp_stats_sums_repeated_splits_for_one_season():
    payload = _vsplayer_payload(
        [
            _split(season=2026, pa=4, ab=4, h=1, so=1, bb=0),
            _split(season=2026, pa=3, ab=2, h=1, so=0, bb=1),
        ]
    )
    assert parse_bvp_stats(payload)["by_season"][2026] == {
        "plateAppearances": 7,
        "atBats": 6,
        "hits": 2,
        "strikeOuts": 1,
        "baseOnBalls": 1,
    }


def test_parse_bvp_stats_season_as_string_is_coerced_to_int():
    payload = _vsplayer_payload([_split(season="2026", pa=4, ab=4, h=1)])
    assert 2026 in parse_bvp_stats(payload)["by_season"]


def test_parse_bvp_stats_derives_career_when_total_group_absent():
    payload = _vsplayer_payload(
        [
            _split(season=2025, pa=5, ab=5, h=2, so=1, bb=0),
            _split(season=2026, pa=3, ab=2, h=1, so=0, bb=1),
        ]
    )
    career = parse_bvp_stats(payload)["career"]
    assert career == {
        "plateAppearances": 8,
        "atBats": 7,
        "hits": 3,
        "strikeOuts": 1,
        "baseOnBalls": 1,
    }


def test_parse_bvp_stats_repeated_total_group_is_harmless():
    payload = _vsplayer_payload([], total=_split(pa=4, ab=4, h=1))
    payload["stats"].append(
        {"type": {"displayName": "vsPlayerTotal"}, "splits": [_split(pa=4, ab=4, h=1)]}
    )
    assert parse_bvp_stats(payload)["career"]["plateAppearances"] == 4


def test_parse_bvp_stats_skips_season_split_without_season():
    payload = _vsplayer_payload([_split(season=None, pa=9, ab=8, h=4)])
    bvp = parse_bvp_stats(payload)
    assert bvp["by_season"] == {}
    assert bvp["career"] is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"stats": []}, {"stats": None}, {"stats": [{"type": {}, "splits": []}]}],
)
def test_parse_bvp_stats_empty_payloads(payload):
    """A payload with no recognized stat group is unresolved, not a zero sample.

    None of these four is the API saying "these two have never faced each other",
    which is a real response with both groups named and empty (below). Treating
    them as resolved would let CALC_71 report a confident "first meeting" off a
    malformed response.
    """
    assert parse_bvp_stats(payload) == {
        "career": None,
        "by_season": {},
        "resolved": False,
    }


def test_a_never_faced_pair_is_resolved_with_no_career():
    """The real shape, measured 2026-08-09: HTTP 200, both groups named, both
    empty. Indistinguishable from a dropped request without `resolved`, and the
    two are opposite answers for CALC_71.
    """
    payload = {
        "stats": [
            {"type": {"displayName": "vsPlayer"}, "splits": []},
            {"type": {"displayName": "vsPlayerTotal"}, "splits": []},
        ]
    }
    assert parse_bvp_stats(payload) == {
        "career": None,
        "by_season": {},
        "resolved": True,
    }


def test_a_faced_pair_is_resolved():
    bvp = parse_bvp_stats(_vsplayer_payload([_split(season=2026, pa=3, ab=3, h=1)]))
    assert bvp["resolved"] is True


# ---- CALC_01 ----


def test_calc_01_career_hit_rate():
    bvp = parse_bvp_stats(
        _vsplayer_payload(
            [
                _split(season=2021, pa=70, ab=54, h=21, so=12, bb=15),
                _split(season=2026, pa=6, ab=6, h=2),
            ]
        )
    )
    result = calc_01_bvp_career_hit_rate(bvp)
    assert result.value == pytest.approx((23 / 76, 76))
    assert result.value.denominator == 76


def test_calc_01_returns_none_without_career_history():
    assert calc_01_bvp_career_hit_rate({"career": None, "by_season": {}}) is None


def test_calc_01_returns_none_on_zero_plate_appearances():
    bvp = {"career": empty_line(), "by_season": {}}
    assert calc_01_bvp_career_hit_rate(bvp) is None


# ---- CALC_02 ----


def test_calc_02_season_hit_rate_isolates_the_requested_season():
    bvp = parse_bvp_stats(
        _vsplayer_payload(
            [
                _split(season=2025, pa=10, ab=10, h=5),
                _split(season=2026, pa=6, ab=6, h=2),
            ]
        )
    )
    assert calc_02_bvp_season_hit_rate(bvp, 2026).value == pytest.approx((2 / 6, 6))
    assert calc_02_bvp_season_hit_rate(bvp, 2025).value == pytest.approx((5 / 10, 10))


def test_calc_02_returns_none_for_unfaced_season():
    bvp = parse_bvp_stats(_vsplayer_payload([_split(season=2024, pa=4, ab=4, h=1)]))
    assert calc_02_bvp_season_hit_rate(bvp, 2026) is None


# ---- CALC_03 ----


@pytest.mark.parametrize(
    "years, expected_pa, expected_hits",
    [(1, 6, 2), (3, 21, 8), (5, 30, 11)],
)
def test_calc_03_recent_window_sums_only_seasons_inside_window(
    years, expected_pa, expected_hits
):
    bvp = parse_bvp_stats(
        _vsplayer_payload(
            [
                _split(season=2022, pa=9, ab=9, h=3),
                _split(season=2024, pa=5, ab=5, h=2),
                _split(season=2025, pa=10, ab=10, h=4),
                _split(season=2026, pa=6, ab=6, h=2),
            ]
        )
    )
    result = calc_03_bvp_recent_window_hit_rate(bvp, 2026, years=years)
    assert result.value == pytest.approx((expected_hits / expected_pa, expected_pa))


def test_calc_03_defaults_to_three_year_window():
    bvp = parse_bvp_stats(
        _vsplayer_payload(
            [
                _split(season=2023, pa=100, ab=100, h=50),
                _split(season=2025, pa=4, ab=4, h=1),
            ]
        )
    )
    assert calc_03_bvp_recent_window_hit_rate(bvp, 2026).value == pytest.approx(
        (1 / 4, 4)
    )


def test_calc_03_ignores_seasons_after_the_reference_season():
    bvp = parse_bvp_stats(_vsplayer_payload([_split(season=2026, pa=6, ab=6, h=3)]))
    assert calc_03_bvp_recent_window_hit_rate(bvp, 2024) is None


def test_calc_03_returns_none_when_window_is_empty():
    bvp = parse_bvp_stats(_vsplayer_payload([_split(season=2019, pa=8, ab=8, h=4)]))
    assert calc_03_bvp_recent_window_hit_rate(bvp, 2026) is None


# ---- CALC_04 ----


def test_calc_04_contact_rate_excludes_strikeouts_and_walks():
    bvp = parse_bvp_stats(
        _vsplayer_payload(
            [
                _split(season=2021, pa=70, ab=54, h=21, so=12, bb=15),
                _split(season=2026, pa=6, ab=6, h=2),
            ]
        )
    )
    assert calc_04_bvp_contact_rate(bvp).value == pytest.approx(
        ((76 - 12 - 15) / 76, 76)
    )


def test_calc_04_scoped_to_a_single_season():
    bvp = parse_bvp_stats(
        _vsplayer_payload([_split(season=2026, pa=10, ab=8, h=3, so=1, bb=2)])
    )
    assert calc_04_bvp_contact_rate(bvp, season=2026).value == pytest.approx(
        (7 / 10, 10)
    )
    assert calc_04_bvp_contact_rate(bvp, season=2025) is None


def test_calc_04_all_outcomes_are_strikeouts_or_walks():
    bvp = parse_bvp_stats(
        _vsplayer_payload([_split(season=2026, pa=4, ab=2, h=0, so=2, bb=2)])
    )
    assert calc_04_bvp_contact_rate(bvp).value == pytest.approx((0.0, 4))


def test_calc_04_returns_none_without_history():
    assert calc_04_bvp_contact_rate({"career": None, "by_season": {}}) is None


# ---- compute_category_01 ----


def test_compute_category_01_returns_all_implemented_keys():
    bvp = parse_bvp_stats(
        _vsplayer_payload(
            [
                _split(season=2025, pa=10, ab=10, h=4, so=2, bb=0),
                _split(season=2026, pa=6, ab=6, h=2, so=1, bb=0),
            ]
        )
    )
    results = compute_category_01(bvp, 2026)
    assert results["CALC_02"].value == pytest.approx((2 / 6, 6))
    assert results["CALC_03"].value == pytest.approx((6 / 16, 16))


def test_compute_category_01_all_none_for_first_time_matchup():
    results = compute_category_01({"career": None, "by_season": {}}, 2026, [])
    assert set(results.values()) == {None}


# ---- CALC_05 ----


@pytest.mark.parametrize(
    "speeds, expected_hard, expected_total",
    [
        ([94.9, 95.0, 95.1], 2, 3),  # 95.0 exactly is hard hit
        ([100.0, 101.0], 2, 2),
        ([60.0], 0, 1),
    ],
)
def test_calc_05_hard_hit_rate(speeds, expected_hard, expected_total):
    pitches = [_pitch("hit_into_play", ev=s) for s in speeds]
    result = calc_05_bvp_hard_hit_rate(pitches)
    assert result.value == pytest.approx(
        (expected_hard / expected_total, expected_total)
    )


def test_calc_05_excludes_batted_balls_without_a_reading():
    pitches = [
        _pitch("hit_into_play", ev=100.0),
        _pitch("hit_into_play", ev=None),  # tracking gap, not a soft-hit ball
    ]
    assert calc_05_bvp_hard_hit_rate(pitches).value == pytest.approx((1.0, 1))


def test_calc_05_ignores_pitches_not_put_in_play():
    pitches = [_pitch("foul", ev=105.0), _pitch("swinging_strike")]
    assert calc_05_bvp_hard_hit_rate(pitches) is None


def test_calc_05_no_batted_balls_returns_none():
    assert calc_05_bvp_hard_hit_rate([]) is None


# ---- CALC_06 ----


def test_calc_06_xba_and_xwoba_average_the_estimates():
    pitches = [
        _pitch("hit_into_play", xba=0.100, xwoba=0.200),
        _pitch("hit_into_play", xba=0.300, xwoba=0.600),
    ]
    assert calc_06_bvp_xba(pitches).value == pytest.approx((0.200, 2))
    assert calc_06_bvp_xwoba(pitches).value == pytest.approx((0.400, 2))


def test_calc_06_drops_batted_balls_missing_that_estimate():
    pitches = [
        _pitch("hit_into_play", xba=0.400, xwoba=None),
        _pitch("hit_into_play", xba=None, xwoba=0.900),
    ]
    assert calc_06_bvp_xba(pitches).value == pytest.approx((0.400, 1))
    assert calc_06_bvp_xwoba(pitches).value == pytest.approx((0.900, 1))


def test_calc_06_no_contact_returns_none():
    assert calc_06_bvp_xba([_pitch("called_strike")]) is None
    assert calc_06_bvp_xwoba([]) is None


# ---- CALC_07 ----


@pytest.mark.parametrize(
    "description, is_swing, is_whiff",
    [
        ("swinging_strike", True, True),
        ("swinging_strike_blocked", True, True),
        ("missed_bunt", True, True),
        ("foul", True, False),
        ("foul_tip", True, False),  # tipped = contact, so a swing but not a miss
        ("foul_bunt", True, False),
        ("bunt_foul_tip", True, False),
        ("hit_into_play", True, False),
        ("ball", False, False),
        ("called_strike", False, False),
        ("blocked_ball", False, False),
        ("hit_by_pitch", False, False),
    ],
)
def test_calc_07_classifies_each_description(description, is_swing, is_whiff):
    """Pins the swing/whiff membership so a later revision is a visible change."""
    result = calc_07_bvp_whiff_rate([_pitch(description)])
    if not is_swing:
        assert result is None
        return
    assert result.value.denominator == 1
    assert result.value.rate == (1.0 if is_whiff else 0.0)


def test_calc_07_whiff_rate_over_mixed_swings():
    pitches = (
        [_pitch("swinging_strike")] * 4
        + [_pitch("foul")] * 6
        + [_pitch("hit_into_play")] * 6
        + [_pitch("ball")] * 9
        + [_pitch("called_strike")]
    )
    assert calc_07_bvp_whiff_rate(pitches).value == pytest.approx((4 / 16, 16))


def test_calc_07_no_swings_returns_none():
    assert calc_07_bvp_whiff_rate([_pitch("ball"), _pitch("called_strike")]) is None


# ---- CALC_08 ----


def test_calc_08_putaway_rate_over_two_strike_pitches():
    pitches = [
        _pitch("foul", strikes=2),
        _pitch("ball", strikes=2),
        _pitch("swinging_strike", events="strikeout", strikes=2),
        _pitch("swinging_strike", strikes=1),  # not a two-strike pitch
    ]
    assert calc_08_bvp_putaway_rate(pitches).value == pytest.approx((1 / 3, 3))


def test_calc_08_counts_strikeout_double_play():
    pitches = [_pitch("swinging_strike", events="strikeout_double_play", strikes=2)]
    assert calc_08_bvp_putaway_rate(pitches).value == pytest.approx((1.0, 1))


def test_calc_08_ignores_non_strikeout_events_ending_two_strike_counts():
    pitches = [_pitch("hit_into_play", events="single", strikes=2)]
    assert calc_08_bvp_putaway_rate(pitches).value == pytest.approx((0.0, 1))


def test_calc_08_no_two_strike_pitches_returns_none():
    assert calc_08_bvp_putaway_rate([_pitch("ball", strikes=1)]) is None


# ---- compute_category_01 with Statcast keys ----


def test_compute_category_01_includes_statcast_keys():
    bvp = parse_bvp_stats(_vsplayer_payload([_split(season=2026, pa=4, ab=4, h=1)]))
    pitches = [_pitch("hit_into_play", ev=99.0, xba=0.5, xwoba=0.6, strikes=2)]
    results = compute_category_01(bvp, 2026, pitches)
    assert sorted(results) == [
        "CALC_01",
        "CALC_02",
        "CALC_03",
        "CALC_04",
        "CALC_05",
        "CALC_06_XBA",
        "CALC_06_XWOBA",
        "CALC_07",
        "CALC_08",
    ]
    assert results["CALC_05"].value == pytest.approx((1.0, 1))


def test_compute_category_01_without_pitches_leaves_statcast_keys_none():
    bvp = parse_bvp_stats(_vsplayer_payload([_split(season=2026, pa=4, ab=4, h=1)]))
    results = compute_category_01(bvp, 2026)
    assert results["CALC_01"] is not None
    for key in ("CALC_05", "CALC_06_XBA", "CALC_06_XWOBA", "CALC_07", "CALC_08"):
        assert results[key] is None
