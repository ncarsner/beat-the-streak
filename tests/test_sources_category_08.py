"""Tests for calculators/sources/category_08_batter_form.py."""

from datetime import date

import pytest

from calculators.sources.category_08_batter_form import (
    CATEGORY_08_PITCH_FIELDS,
    fetch_batter_form,
)
from tests.conftest import _install_fake_pybaseball, _statcast_frame


def _rows(n=2, **overrides):
    base = {
        "game_date": "2026-07-01",
        "game_pk": 745000,
        "game_type": "R",
        "at_bat_number": 12,
        "pitch_number": 1,
        "events": None,
        "description": "ball",
        "launch_speed": None,
        "launch_angle": None,
        "estimated_ba_using_speedangle": None,
        "estimated_woba_using_speedangle": None,
        "zone": 5,  # not a Category 8 field; must be projected away
        "release_speed": 94.0,  # not a Category 8 field; must be projected away
    }
    base.update(overrides)
    return [dict(base, pitch_number=i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Field projection
# ---------------------------------------------------------------------------


def test_game_type_is_present():
    """The load-bearing field for this category. Without it every recency window
    silently includes spring training, which was 7.8% of the probe batter's
    pitches and carries no expected statistics at all.
    """
    assert "game_type" in CATEGORY_08_PITCH_FIELDS


@pytest.mark.parametrize(
    "field",
    [
        "game_date",
        "game_pk",
        "game_type",
        "at_bat_number",
        "pitch_number",
        "events",
        "description",
        "launch_speed",
        "launch_angle",
        "estimated_ba_using_speedangle",
        "estimated_woba_using_speedangle",
    ],
)
def test_every_field_a_calculator_reads_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert field in fetch_batter_form(592450, 2026)[0]


def test_columns_outside_the_field_set_are_dropped(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    record = fetch_batter_form(592450, 2026)[0]
    assert "zone" not in record
    assert "release_speed" not in record


def test_one_record_per_pitch(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(5)))
    assert len(fetch_batter_form(592450, 2026)) == 5


def test_spring_training_rows_survive_the_fetch(monkeypatch):
    """The fetcher does not filter. Excluding non-competitive games is the
    calculators' job, so a source test can still see what the season really
    contained."""
    _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame(_rows(1, game_type="S"))
    )
    assert fetch_batter_form(592450, 2026)[0]["game_type"] == "S"


def test_pandas_missing_sentinels_normalize_to_none(monkeypatch):
    """Statcast leaves launch readings and expected stats null on the batted balls
    it cannot measure, and every reader treats a null as no reading."""
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    record = fetch_batter_form(592450, 2026)[0]
    assert record["launch_speed"] is None
    assert record["launch_angle"] is None
    assert record["estimated_ba_using_speedangle"] is None


# ---------------------------------------------------------------------------
# Side selection, cache sharing, error contract
# ---------------------------------------------------------------------------


def test_the_fetcher_pulls_the_batter_side(monkeypatch):
    """A completed season, so the end date is the year's and not today's."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    fetch_batter_form(592450, 2024)
    assert calls == [("2024-01-01", "2024-12-31", 592450)]


def test_the_current_season_pull_stops_at_today(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    today = date.today()
    fetch_batter_form(592450, today.year)
    assert calls == [(f"{today.year}-01-01", today.isoformat(), 592450)]


def test_a_second_fetch_of_the_same_player_and_season_costs_no_second_call(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(2)))
    first = fetch_batter_form(592450, 2026)
    second = fetch_batter_form(592450, 2026)
    assert len(calls) == 1
    assert first == second


def test_the_category_4_fetcher_reuses_the_same_cached_frame(monkeypatch):
    """Different field projections, one network call. The projection happens after
    the cache, which is why the field tuples can differ per category."""
    from calculators.sources.category_04_plate_discipline import (
        fetch_batter_discipline,
    )

    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    category_08 = fetch_batter_form(592450, 2026)
    category_04 = fetch_batter_discipline(592450, 2026)
    assert len(calls) == 1
    assert "launch_speed" in category_08[0] and "zone" not in category_08[0]
    assert "zone" in category_04[0] and "launch_speed" not in category_04[0]


def test_a_pre_statcast_season_returns_empty_without_a_network_call(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert fetch_batter_form(592450, 2014) == []
    assert calls == []


def test_a_failing_pull_returns_empty_rather_than_raising(monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant is down"))
    assert fetch_batter_form(592450, 2026) == []


@pytest.mark.parametrize("frame", [None, "empty"])
def test_an_empty_or_missing_frame_returns_empty(frame, monkeypatch):
    _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame([]) if frame == "empty" else None
    )
    assert fetch_batter_form(592450, 2026) == []
