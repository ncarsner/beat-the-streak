"""Tests for calculators/sources/category_10_defense_schedule.py."""

from datetime import date, datetime, timezone

import pytest
import requests

from calculators.sources.category_10_defense_schedule import (
    DEFAULT_LOOKBACK_DAYS,
    HOME_PLATE,
    fetch_home_plate_umpire,
    fetch_team_game_log,
)
from tests.conftest import FakeResponse


def _schedule(*games):
    """A raw /schedule response. *games* are dicts of overrides."""
    dates = {}
    for overrides in games:
        game = {
            "gamePk": 1,
            "officialDate": "2026-08-03",
            "gameNumber": 1,
            "dayNight": "night",
            "venue": {"id": 3313, "name": "Yankee Stadium"},
            "gameDate": "2026-08-03T23:05:00Z",
        }
        game.update(overrides)
        dates.setdefault(game["officialDate"], []).append(game)
    return {
        "dates": [{"date": day, "games": games} for day, games in sorted(dates.items())]
    }


def _officials(*entries):
    return {
        "officials": [
            {"official": {"id": pid, "fullName": name}, "officialType": kind}
            for pid, name, kind in entries
        ]
    }


# ---------------------------------------------------------------------------
# fetch_team_game_log
# ---------------------------------------------------------------------------


def test_the_window_ends_at_the_requested_date(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_schedule({}))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_team_game_log(147, date(2026, 8, 9), lookback_days=14)
    assert seen["endDate"] == "2026-08-09"
    assert seen["startDate"] == "2026-07-26"
    assert seen["teamId"] == 147


def test_only_regular_season_games_are_requested(monkeypatch):
    """Spring training and exhibitions are not a fatigue signal for a season
    projection, and this is the one category that can filter at the request."""
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_schedule({}))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_team_game_log(147)
    assert seen["gameType"] == "R"


def test_the_lookback_defaults_to_two_weeks(monkeypatch):
    """CALC_69 and CALC_70 only look at the immediately preceding game, so this
    only has to cover the longest plausible gap between two games."""
    assert DEFAULT_LOOKBACK_DAYS == 14


def test_one_request_covers_the_whole_window(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(params)
        return FakeResponse(_schedule({"gamePk": 1}, {"gamePk": 2}))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_team_game_log(147)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field",
    ["game_pk", "game_date", "game_number", "day_night", "venue_id", "start_time"],
)
def test_every_field_the_calculators_read_is_projected(field, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(_schedule({})))
    assert field in fetch_team_game_log(147)[0]


def test_the_start_time_is_parsed_to_an_aware_datetime(monkeypatch):
    """CALC_69's turnaround subtracts two of these, which a naive datetime and an
    aware one cannot do together."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(_schedule({})))
    start = fetch_team_game_log(147)[0]["start_time"]
    assert start == datetime(2026, 8, 3, 23, 5, tzinfo=timezone.utc)


def test_an_unparseable_start_time_is_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse(_schedule({"gameDate": "soon"}))
    )
    assert fetch_team_game_log(147)[0]["start_time"] is None


def test_games_are_returned_oldest_first(monkeypatch):
    payload = _schedule(
        {"gamePk": 3, "officialDate": "2026-08-03"},
        {"gamePk": 1, "officialDate": "2026-08-01"},
        {"gamePk": 2, "officialDate": "2026-08-02"},
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert [g["game_pk"] for g in fetch_team_game_log(147)] == [1, 2, 3]


def test_a_doubleheader_is_ordered_by_game_number(monkeypatch):
    """Both halves share a date, so the date alone cannot order them, and
    `previous_game` depends on this ordering being right."""
    payload = _schedule(
        {"gamePk": 20, "officialDate": "2026-08-02", "gameNumber": 2},
        {"gamePk": 10, "officialDate": "2026-08-02", "gameNumber": 1},
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert [g["game_pk"] for g in fetch_team_game_log(147)] == [10, 20]


def test_a_missing_game_number_defaults_to_one(monkeypatch):
    payload = _schedule({"gameNumber": None})
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_team_game_log(147)[0]["game_number"] == 1


def test_a_failing_request_returns_an_empty_log(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_team_game_log(147) == []


def test_an_http_error_returns_an_empty_log(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse({}, status_code=503)
    )
    assert fetch_team_game_log(147) == []


def test_an_empty_schedule_returns_an_empty_log(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse({"dates": []}))
    assert fetch_team_game_log(147) == []


# ---------------------------------------------------------------------------
# fetch_home_plate_umpire
# ---------------------------------------------------------------------------


def test_the_plate_umpire_is_selected_from_the_crew(monkeypatch):
    payload = _officials(
        (1, "Base One", "First Base"),
        (605670, "Dan Merzel", HOME_PLATE),
        (3, "Base Two", "Second Base"),
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_home_plate_umpire(745000) == {"id": 605670, "fullName": "Dan Merzel"}


def test_a_crew_without_a_plate_umpire_yields_none(monkeypatch):
    payload = _officials((1, "Base One", "First Base"))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_home_plate_umpire(745000) is None


def test_an_unpublished_assignment_yields_none(monkeypatch):
    """Before a game this is the common case rather than an error: officials
    publish on the same later clock as the weather."""
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse({"officials": []})
    )
    assert fetch_home_plate_umpire(745000) is None


def test_an_official_without_an_id_is_skipped(monkeypatch):
    payload = {"officials": [{"official": {}, "officialType": HOME_PLATE}]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_home_plate_umpire(745000) is None


def test_it_reads_the_boxscore_for_the_requested_game(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return FakeResponse({"officials": []})

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_home_plate_umpire(745123)
    assert seen["url"].endswith("/game/745123/boxscore")


def test_a_failing_umpire_request_returns_none(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_home_plate_umpire(745000) is None
