"""Tests for calculators/sources/category_03_pitch_arsenal.py."""

from datetime import date

import pytest

from calculators.sources.category_03_pitch_arsenal import (
    CATEGORY_03_PITCH_FIELDS,
    fetch_batter_arsenal,
    fetch_pitcher_arsenal,
)
from tests.conftest import _install_fake_pybaseball, _statcast_frame


def _rows(n=2, **overrides):
    base = {
        "game_date": "2026-07-01",
        "game_pk": 745000,
        "at_bat_number": 12,
        "pitch_number": 1,
        "events": None,
        "description": "ball",
        "pitch_type": "FF",
        "release_speed": 94.1,
        "effective_speed": 94.6,
        "release_extension": 6.5,
        "delta_run_exp": 0.031,
        "pfx_x": -0.42,
        "vy0": -131.02,
        "ay": 24.14,
        "vz0": -4.93,
        "az": -20.93,
        "attack_angle": 8.4,
        "estimated_ba_using_speedangle": 0.312,
        "launch_speed": 98.2,  # not a Category 3 field; must be projected away
    }
    base.update(overrides)
    return [dict(base, pitch_number=i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Field projection
# ---------------------------------------------------------------------------


def test_the_two_silently_failing_fields_are_present():
    """`pitch_number` and `ay` both fail as absence rather than as an error.

    Without `pitch_number` the terminal-pitch reduction has nothing to rank on;
    without `ay` the approach-angle solution returns None for every pitch, which
    reads as "no Statcast coverage" rather than as a missing column.
    """
    assert "pitch_number" in CATEGORY_03_PITCH_FIELDS
    assert "ay" in CATEGORY_03_PITCH_FIELDS


@pytest.mark.parametrize(
    "field",
    [
        "game_date",
        "game_pk",
        "at_bat_number",
        "pitch_number",
        "events",
        "pitch_type",
        "release_speed",
        "effective_speed",
        "release_extension",
        "delta_run_exp",
        "pfx_x",
        "vy0",
        "ay",
        "vz0",
        "az",
        "attack_angle",
        "estimated_ba_using_speedangle",
    ],
)
def test_every_field_a_calculator_reads_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert field in fetch_pitcher_arsenal(543037, 2026)[0]


def test_columns_outside_the_field_set_are_dropped(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert "launch_speed" not in fetch_pitcher_arsenal(543037, 2026)[0]


def test_one_record_per_pitch(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(5)))
    assert len(fetch_pitcher_arsenal(543037, 2026)) == 5


def test_a_pre_bat_tracking_season_omits_attack_angle_rather_than_faking_it(
    monkeypatch,
):
    """The column does not exist before 2023, and a fabricated None would be
    indistinguishable from a real missing reading."""
    rows = [{k: v for k, v in row.items() if k != "attack_angle"} for row in _rows(1)]
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(rows))
    record = fetch_batter_arsenal(592450, 2018)
    assert record and "attack_angle" not in record[0]
    assert "release_speed" in record[0]


def test_pandas_missing_sentinels_normalize_to_none(monkeypatch):
    """No calculator ever sees a NaN — `np.nan` fails its own equality test."""
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(
            _rows(1, estimated_ba_using_speedangle=None, events=None)
        ),
    )
    record = fetch_pitcher_arsenal(543037, 2026)[0]
    assert record["estimated_ba_using_speedangle"] is None
    assert record["events"] is None


# ---------------------------------------------------------------------------
# Side selection
# ---------------------------------------------------------------------------


def test_the_pitcher_fetcher_pulls_the_pitcher_side(monkeypatch):
    """A completed season, so the end date is the year's and not today's."""
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    fetch_pitcher_arsenal(543037, 2024)
    assert calls == [("pitcher", "2024-01-01", "2024-12-31", 543037)]


def test_the_batter_fetcher_pulls_the_batter_side(monkeypatch):
    """The two sides are different rows; serving one for the other is silent."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    fetch_batter_arsenal(592450, 2024)
    assert calls == [("2024-01-01", "2024-12-31", 592450)]


def test_the_current_season_pull_stops_at_today(monkeypatch):
    """Asking Savant for the rest of the year would be a request for future games."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    today = date.today()
    fetch_batter_arsenal(592450, today.year)
    assert calls == [(f"{today.year}-01-01", today.isoformat(), 592450)]


def test_the_two_fetchers_return_different_sides(monkeypatch):
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1, pitch_type="CH")),
        pitcher_frame=_statcast_frame(_rows(1, pitch_type="SL")),
    )
    assert fetch_batter_arsenal(592450, 2026)[0]["pitch_type"] == "CH"
    assert fetch_pitcher_arsenal(543037, 2026)[0]["pitch_type"] == "SL"


# ---------------------------------------------------------------------------
# Cache sharing and error contract
# ---------------------------------------------------------------------------


def test_a_second_fetch_of_the_same_player_and_season_costs_no_second_call(monkeypatch):
    """The cache is keyed (role, player_id, season) and stores the raw frame, so
    a Category 2 pull and a Category 3 pull of the same player share one call."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(2)))
    first = fetch_batter_arsenal(592450, 2026)
    second = fetch_batter_arsenal(592450, 2026)
    assert len(calls) == 1
    assert first == second


def test_the_category_2_fetcher_reuses_the_same_cached_frame(monkeypatch):
    """Different field projections, one network call — the projection happens
    after the cache, which is why the field tuples can differ per category."""
    from calculators.sources.category_02_platoon_splits import fetch_batter_statcast

    calls = _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame(_rows(1, stand="R", p_throws="L"))
    )
    fetch_batter_arsenal(592450, 2026)
    category_02 = fetch_batter_statcast(592450, 2026)
    assert len(calls) == 1
    assert "pitch_type" not in category_02[0]  # not a Category 2 field
    assert "stand" in category_02[0]


@pytest.mark.parametrize("fetcher", [fetch_pitcher_arsenal, fetch_batter_arsenal])
def test_a_pre_statcast_season_returns_empty_without_a_network_call(
    fetcher, monkeypatch
):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert fetcher(543037, 2014) == []
    assert calls == []


@pytest.mark.parametrize("fetcher", [fetch_pitcher_arsenal, fetch_batter_arsenal])
def test_a_failing_pull_returns_empty_rather_than_raising(fetcher, monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant is down"))
    assert fetcher(543037, 2026) == []


@pytest.mark.parametrize("fetcher", [fetch_pitcher_arsenal, fetch_batter_arsenal])
def test_an_empty_frame_returns_empty(fetcher, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    assert fetcher(543037, 2026) == []


@pytest.mark.parametrize("fetcher", [fetch_pitcher_arsenal, fetch_batter_arsenal])
def test_a_none_frame_returns_empty(fetcher, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=None)
    assert fetcher(543037, 2026) == []
