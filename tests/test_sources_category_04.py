"""Tests for calculators/sources/category_04_plate_discipline.py."""

from datetime import date

import pytest

from calculators.sources.category_04_plate_discipline import (
    CATEGORY_04_PITCH_FIELDS,
    fetch_batter_discipline,
    fetch_pitcher_discipline,
)
from tests.conftest import _install_fake_pybaseball, _statcast_frame


def _rows(n=2, **overrides):
    base = {
        "game_date": "2026-07-01",
        "game_pk": 745000,
        "at_bat_number": 12,
        "pitch_number": 1,
        "events": None,
        "description": "called_strike",
        "type": "S",
        "zone": 5,
        "balls": 0,
        "strikes": 0,
        "estimated_ba_using_speedangle": None,
        "plate_x": 0.11,  # not a Category 4 field; must be projected away
        "launch_speed": 98.2,  # not a Category 4 field; must be projected away
    }
    base.update(overrides)
    return [dict(base, pitch_number=i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Field projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "game_date",
        "game_pk",
        "at_bat_number",
        "pitch_number",
        "events",
        "description",
        "type",
        "zone",
        "balls",
        "strikes",
        "estimated_ba_using_speedangle",
    ],
)
def test_every_field_a_calculator_reads_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert field in fetch_pitcher_discipline(543037, 2026)[0]


@pytest.mark.parametrize("field", ["plate_x", "plate_z", "sz_top", "sz_bot"])
def test_the_coordinate_columns_are_deliberately_absent(field):
    """`zone` is used as published, not reconstructed from coordinates: a
    reconstruction disagreed with Statcast's own zone on 4.3 percent of tracked
    pitches. Listing columns nobody reads would make this tuple a claim about the
    category that is not true.
    """
    assert field not in CATEGORY_04_PITCH_FIELDS


def test_columns_outside_the_field_set_are_dropped(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    record = fetch_pitcher_discipline(543037, 2026)[0]
    assert "launch_speed" not in record
    assert "plate_x" not in record


def test_one_record_per_pitch(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(5)))
    assert len(fetch_pitcher_discipline(543037, 2026)) == 5


def test_an_untracked_pitch_keeps_a_null_zone_rather_than_being_dropped(monkeypatch):
    """A pitch-timer violation carries no location. It has to survive the fetch so
    the count-state calculators can see it and exclude it by name; silently
    dropping it here would hide it from CALC_28.
    """
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1, zone=None, description="automatic_ball")),
    )
    record = fetch_batter_discipline(592450, 2026)[0]
    assert record["zone"] is None
    assert record["description"] == "automatic_ball"


def test_pandas_missing_sentinels_normalize_to_none(monkeypatch):
    """No calculator ever sees a NaN. `np.nan` fails its own equality test, and
    `zone_code` would coerce it to a bogus int if it arrived."""
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(
            _rows(1, estimated_ba_using_speedangle=None, events=None, zone=None)
        ),
    )
    record = fetch_pitcher_discipline(543037, 2026)[0]
    assert record["estimated_ba_using_speedangle"] is None
    assert record["events"] is None
    assert record["zone"] is None


# ---------------------------------------------------------------------------
# Side selection
# ---------------------------------------------------------------------------


def test_the_pitcher_fetcher_pulls_the_pitcher_side(monkeypatch):
    """A completed season, so the end date is the year's and not today's."""
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    fetch_pitcher_discipline(543037, 2024)
    assert calls == [("pitcher", "2024-01-01", "2024-12-31", 543037)]


def test_the_batter_fetcher_pulls_the_batter_side(monkeypatch):
    """The two sides are different rows; serving one for the other is silent."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    fetch_batter_discipline(592450, 2024)
    assert calls == [("2024-01-01", "2024-12-31", 592450)]


def test_the_current_season_pull_stops_at_today(monkeypatch):
    """Asking Savant for the rest of the year would be a request for future games."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    today = date.today()
    fetch_batter_discipline(592450, today.year)
    assert calls == [(f"{today.year}-01-01", today.isoformat(), 592450)]


def test_the_two_fetchers_return_different_sides(monkeypatch):
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1, zone=1)),
        pitcher_frame=_statcast_frame(_rows(1, zone=9)),
    )
    assert fetch_batter_discipline(592450, 2026)[0]["zone"] == 1
    assert fetch_pitcher_discipline(543037, 2026)[0]["zone"] == 9


# ---------------------------------------------------------------------------
# Cache sharing and error contract
# ---------------------------------------------------------------------------


def test_a_second_fetch_of_the_same_player_and_season_costs_no_second_call(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(2)))
    first = fetch_batter_discipline(592450, 2026)
    second = fetch_batter_discipline(592450, 2026)
    assert len(calls) == 1
    assert first == second


def test_the_category_3_fetcher_reuses_the_same_cached_frame(monkeypatch):
    """Different field projections, one network call. The projection happens after
    the cache, which is why the field tuples can differ per category."""
    from calculators.sources.category_03_pitch_arsenal import fetch_batter_arsenal

    calls = _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1, pitch_type="FF", release_speed=95.0)),
    )
    category_04 = fetch_batter_discipline(592450, 2026)
    category_03 = fetch_batter_arsenal(592450, 2026)
    assert len(calls) == 1
    assert "zone" in category_04[0] and "release_speed" not in category_04[0]
    assert "release_speed" in category_03[0] and "zone" not in category_03[0]


@pytest.mark.parametrize("fetcher", [fetch_pitcher_discipline, fetch_batter_discipline])
def test_a_pre_statcast_season_returns_empty_without_a_network_call(
    fetcher, monkeypatch
):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert fetcher(543037, 2014) == []
    assert calls == []


@pytest.mark.parametrize("fetcher", [fetch_pitcher_discipline, fetch_batter_discipline])
def test_a_failing_pull_returns_empty_rather_than_raising(fetcher, monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant is down"))
    assert fetcher(543037, 2026) == []


@pytest.mark.parametrize("fetcher", [fetch_pitcher_discipline, fetch_batter_discipline])
def test_an_empty_frame_returns_empty(fetcher, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    assert fetcher(543037, 2026) == []


@pytest.mark.parametrize("fetcher", [fetch_pitcher_discipline, fetch_batter_discipline])
def test_a_none_frame_returns_empty(fetcher, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=None)
    assert fetcher(543037, 2026) == []
