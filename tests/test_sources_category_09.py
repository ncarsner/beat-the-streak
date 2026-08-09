"""Tests for calculators/sources/category_09_pitcher_form.py."""

from datetime import date

import pytest

from calculators.sources.category_09_pitcher_form import (
    CATEGORY_09_PITCH_FIELDS,
    fetch_pitcher_form,
)
from tests.conftest import _install_fake_pybaseball, _statcast_frame


def _rows(n=2, **overrides):
    base = {
        "game_date": "2026-07-01",
        "game_pk": 745000,
        "game_type": "R",
        "at_bat_number": 1,
        "pitch_number": 1,
        "inning": 1,
        "inning_topbot": "Top",
        "outs_when_up": 0,
        "events": None,
        "description": "ball",
        "pitch_type": "FF",
        "release_speed": 94.0,
        "zone": 5,
        "launch_speed": None,
        "bat_score": 0,
        "post_bat_score": 0,
        "arm_angle": 42.0,  # not a Category 9 field; must be projected away
    }
    base.update(overrides)
    return [dict(base, pitch_number=i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Field projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["inning", "inning_topbot", "outs_when_up", "bat_score", "post_bat_score"]
)
def test_the_reconstruction_fields_are_present(field):
    """These five exist only for this category. `inning` and `outs_when_up`
    identify a start and rebuild innings pitched, neither of which Statcast
    publishes; the score pair gives runs allowed. Without them the category has
    no start concept at all.
    """
    assert field in CATEGORY_09_PITCH_FIELDS


@pytest.mark.parametrize("field", list(CATEGORY_09_PITCH_FIELDS))
def test_every_field_a_calculator_reads_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame(_rows(1)))
    assert field in fetch_pitcher_form(668678, 2026)[0]


def test_columns_outside_the_field_set_are_dropped(monkeypatch):
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame(_rows(1)))
    assert "arm_angle" not in fetch_pitcher_form(668678, 2026)[0]


def test_one_record_per_pitch(monkeypatch):
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame(_rows(5)))
    assert len(fetch_pitcher_form(668678, 2026)) == 5


def test_spring_training_rows_survive_the_fetch(monkeypatch):
    """The fetcher does not filter; excluding non-competitive games is the
    calculators' job."""
    _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1, game_type="S"))
    )
    assert fetch_pitcher_form(668678, 2026)[0]["game_type"] == "S"


def test_pandas_missing_sentinels_normalize_to_none(monkeypatch):
    _install_fake_pybaseball(
        monkeypatch,
        pitcher_frame=_statcast_frame(
            _rows(1, launch_speed=None, events=None, zone=None)
        ),
    )
    record = fetch_pitcher_form(668678, 2026)[0]
    assert record["launch_speed"] is None
    assert record["events"] is None
    assert record["zone"] is None


# ---------------------------------------------------------------------------
# Side selection, cache sharing, error contract
# ---------------------------------------------------------------------------


def test_the_fetcher_pulls_the_pitcher_side(monkeypatch):
    """The batter frame would be a different set of rows entirely, and serving one
    for the other is silent."""
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    fetch_pitcher_form(668678, 2024)
    assert calls == [("pitcher", "2024-01-01", "2024-12-31", 668678)]


def test_the_current_season_pull_stops_at_today(monkeypatch):
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    today = date.today()
    fetch_pitcher_form(668678, today.year)
    assert calls == [("pitcher", f"{today.year}-01-01", today.isoformat(), 668678)]


def test_a_second_fetch_of_the_same_pitcher_and_season_costs_no_second_call(
    monkeypatch,
):
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(2))
    )
    first = fetch_pitcher_form(668678, 2026)
    second = fetch_pitcher_form(668678, 2026)
    assert len(calls) == 1
    assert first == second


def test_the_category_3_fetcher_reuses_the_same_cached_frame(monkeypatch):
    """Different field projections, one network call."""
    from calculators.sources.category_03_pitch_arsenal import fetch_pitcher_arsenal

    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    category_09 = fetch_pitcher_form(668678, 2026)
    category_03 = fetch_pitcher_arsenal(668678, 2026)
    assert len(calls) == 1
    assert "inning" in category_09[0] and "release_extension" not in category_09[0]
    assert "inning" not in category_03[0]


def test_a_pre_statcast_season_returns_empty_without_a_network_call(monkeypatch):
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    assert fetch_pitcher_form(668678, 2014) == []
    assert calls == []


def test_a_failing_pull_returns_empty_rather_than_raising(monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant is down"))
    assert fetch_pitcher_form(668678, 2026) == []


@pytest.mark.parametrize("frame", [None, "empty"])
def test_an_empty_or_missing_frame_returns_empty(frame, monkeypatch):
    _install_fake_pybaseball(
        monkeypatch,
        pitcher_frame=_statcast_frame([]) if frame == "empty" else None,
    )
    assert fetch_pitcher_form(668678, 2026) == []
