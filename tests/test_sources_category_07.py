"""Tests for calculators/sources/category_07_bullpen_exposure.py."""

from datetime import date

import pytest
import requests

from calculators.sources.category_07_bullpen_exposure import (
    ACTIVE_STATUS,
    CATEGORY_07_PITCH_FIELDS,
    PITCHER_POSITION,
    attach_category_07,
    empty_bullpen,
    fetch_active_pitchers,
    fetch_bullpen,
    fetch_pitching_game_logs,
    fetch_reliever_pitches,
)
from tests.conftest import FakeResponse, _install_fake_pybaseball, _statcast_frame

TODAY = date(2026, 8, 9)


def _roster_entry(
    player_id=1,
    name="Reliever One",
    hand="R",
    position=PITCHER_POSITION,
    status=ACTIVE_STATUS,
):
    person = {"id": player_id, "fullName": name}
    if hand is not None:
        person["pitchHand"] = {"code": hand}
    return {
        "person": person,
        "position": {"abbreviation": position},
        "status": {"code": status},
    }


def _roster(*entries):
    return {"roster": list(entries)}


def _log_split(
    player_id=1,
    game_date="2026-08-08",
    game_type="R",
    started=0,
    **stat,
):
    line = {
        "battersFaced": 4,
        "atBats": 4,
        "hits": 1,
        "baseOnBalls": 0,
        "hitByPitch": 0,
        "strikeOuts": 1,
        "numberOfPitches": 15,
        "outs": 3,
        "holds": 0,
        "saves": 0,
        "gamesStarted": started,
    }
    line.update(stat)
    return {
        "date": game_date,
        "gameType": game_type,
        "game": {"gamePk": 1},
        "stat": line,
    }


def _people(*people):
    return {"people": list(people)}


def _person_log(player_id=1, splits=()):
    return {
        "id": player_id,
        "stats": [{"group": {"displayName": "pitching"}, "splits": list(splits)}],
    }


# ---------------------------------------------------------------------------
# fetch_active_pitchers
# ---------------------------------------------------------------------------


def test_the_roster_request_is_date_scoped(monkeypatch):
    """Probed 2026-08-09: the endpoint honours `date`, returning different
    pitcher sets in April and August. A backtest depends on it."""
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_roster(_roster_entry()))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_active_pitchers(147, TODAY)
    assert seen["date"] == "2026-08-09"
    assert seen["rosterType"] == "active"


def test_the_roster_request_hydrates_the_throwing_hand(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_roster(_roster_entry()))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_active_pitchers(147, TODAY)
    assert "pitchHand" in seen["hydrate"]


def test_only_pitchers_are_returned(monkeypatch):
    payload = _roster(
        _roster_entry(1, "Reliever", position="P"),
        _roster_entry(2, "Catcher", position="C"),
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert [p["id"] for p in fetch_active_pitchers(147, TODAY)] == [1]


def test_an_inactive_pitcher_is_dropped(monkeypatch):
    """A pitcher on the injured list is not in tonight's bullpen."""
    payload = _roster(
        _roster_entry(1, status=ACTIVE_STATUS), _roster_entry(2, status="D60")
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert [p["id"] for p in fetch_active_pitchers(147, TODAY)] == [1]


def test_an_entry_without_a_player_id_is_skipped(monkeypatch):
    payload = {
        "roster": [
            {
                "person": {"fullName": "Nobody"},
                "position": {"abbreviation": "P"},
                "status": {"code": "A"},
            }
        ]
    }
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_active_pitchers(147, TODAY) == []


def test_an_unresolved_throwing_hand_is_none_rather_than_missing(monkeypatch):
    payload = _roster(_roster_entry(hand=None))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_active_pitchers(147, TODAY)[0]["pitch_hand"] is None


def test_a_failing_roster_request_returns_an_empty_list(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_active_pitchers(147, TODAY) == []


def test_an_http_error_returns_an_empty_roster(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse({}, status_code=503)
    )
    assert fetch_active_pitchers(147, TODAY) == []


# ---------------------------------------------------------------------------
# fetch_pitching_game_logs
# ---------------------------------------------------------------------------


def test_a_whole_bullpen_costs_one_game_log_request(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(params)
        return FakeResponse(_people(_person_log(1, [_log_split()])))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_pitching_game_logs(list(range(1, 14)), 2026)
    assert len(calls) == 1
    assert calls[0]["personIds"].count(",") == 12


def test_the_game_log_request_asks_for_the_pitching_group(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_people())

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_pitching_game_logs([1], 2026)
    assert "group=[pitching]" in seen["hydrate"]
    assert "type=[gameLog]" in seen["hydrate"]
    assert "season=2026" in seen["hydrate"]


@pytest.mark.parametrize(
    "field",
    [
        "game_date",
        "game_type",
        "game_pk",
        "started",
        "batters_faced",
        "at_bats",
        "hits",
        "walks",
        "hit_by_pitch",
        "strike_outs",
        "pitches",
        "outs",
        "holds",
        "saves",
    ],
)
def test_every_field_the_calculators_read_is_projected(field, monkeypatch):
    payload = _people(_person_log(1, [_log_split()]))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert field in fetch_pitching_game_logs([1], 2026)[1][0]


def test_games_started_becomes_a_boolean(monkeypatch):
    payload = _people(_person_log(1, [_log_split(started=1), _log_split(started=0)]))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert [g["started"] for g in fetch_pitching_game_logs([1], 2026)[1]] == [
        True,
        False,
    ]


def test_the_game_type_is_carried_so_the_calculators_can_filter(monkeypatch):
    """Issue #42 exists because Categories 1-4 could not filter spring games.
    Here the column is free."""
    payload = _people(_person_log(1, [_log_split(game_type="S")]))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_pitching_game_logs([1], 2026)[1][0]["game_type"] == "S"


def test_a_missing_counting_field_normalizes_to_zero(monkeypatch):
    payload = _people(_person_log(1, [{"date": "2026-08-08", "stat": {}}]))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_pitching_game_logs([1], 2026)[1][0]["batters_faced"] == 0


def test_a_pitcher_with_no_log_is_absent_from_the_mapping(monkeypatch):
    payload = _people(_person_log(1, []))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_pitching_game_logs([1], 2026) == {}


def test_no_ids_means_no_request(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not have been called")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_pitching_game_logs([], 2026) == {}


def test_a_failing_game_log_request_returns_an_empty_mapping(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_pitching_game_logs([1], 2026) == {}


# ---------------------------------------------------------------------------
# fetch_bullpen
# ---------------------------------------------------------------------------


def test_a_whole_bullpen_costs_exactly_two_requests(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(url)
        if url.endswith("/roster"):
            return FakeResponse(_roster(*(_roster_entry(i) for i in range(1, 14))))
        return FakeResponse(
            _people(*(_person_log(i, [_log_split()]) for i in range(1, 14)))
        )

    monkeypatch.setattr(requests, "get", fake_get)
    pen = fetch_bullpen(147, TODAY, 2026)
    assert len(calls) == 2
    assert len(pen) == 13


def test_every_active_pitcher_is_returned_starters_included(monkeypatch):
    """Membership is a judgement over the game log, so it belongs in the
    calculator module where it is pure and testable."""

    def fake_get(url, params=None, **kwargs):
        if url.endswith("/roster"):
            return FakeResponse(_roster(_roster_entry(1), _roster_entry(2)))
        return FakeResponse(
            _people(
                _person_log(1, [_log_split(started=0)]),
                _person_log(2, [_log_split(started=1, battersFaced=24)]),
            )
        )

    monkeypatch.setattr(requests, "get", fake_get)
    assert len(fetch_bullpen(147, TODAY, 2026)) == 2


def test_a_pitcher_with_no_log_still_carries_an_empty_games_list(monkeypatch):
    def fake_get(url, params=None, **kwargs):
        if url.endswith("/roster"):
            return FakeResponse(_roster(_roster_entry(1)))
        return FakeResponse(_people())

    monkeypatch.setattr(requests, "get", fake_get)
    assert fetch_bullpen(147, TODAY, 2026)[0]["games"] == []


def test_an_empty_roster_skips_the_game_log_request(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(url)
        return FakeResponse(_roster())

    monkeypatch.setattr(requests, "get", fake_get)
    assert fetch_bullpen(147, TODAY, 2026) == empty_bullpen()
    assert len(calls) == 1


def test_the_season_defaults_to_the_roster_dates_year(monkeypatch):
    seen = []

    def fake_get(url, params=None, **kwargs):
        seen.append(params or {})
        if url.endswith("/roster"):
            return FakeResponse(_roster(_roster_entry(1)))
        return FakeResponse(_people(_person_log(1, [_log_split()])))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_bullpen(147, date(2024, 5, 1))
    assert "season=2024" in seen[1]["hydrate"]


def test_attach_returns_the_aggregate_argument(monkeypatch):
    def fake_get(url, params=None, **kwargs):
        if url.endswith("/roster"):
            return FakeResponse(_roster(_roster_entry(1)))
        return FakeResponse(_people(_person_log(1, [_log_split()])))

    monkeypatch.setattr(requests, "get", fake_get)
    assert set(attach_category_07(147, TODAY, 2026)) == {"bullpen"}


# ---------------------------------------------------------------------------
# fetch_reliever_pitches
# ---------------------------------------------------------------------------


def test_the_whiff_fetcher_projects_the_pitch_fields(monkeypatch):
    frame = _statcast_frame(
        [
            {
                "game_date": "2026-08-08",
                "game_pk": 1,
                "game_type": "R",
                "at_bat_number": 1,
                "pitch_number": 1,
                "description": "swinging_strike",
                "events": None,
            }
        ]
    )
    _install_fake_pybaseball(monkeypatch, pitcher_frame=frame)
    records = fetch_reliever_pitches(1, 2026)
    assert set(records[0]) == set(CATEGORY_07_PITCH_FIELDS)


def test_an_empty_statcast_frame_returns_no_pitches(monkeypatch):
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame([]))
    assert fetch_reliever_pitches(1, 2026) == []


def test_a_failing_statcast_pull_returns_no_pitches(monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant down"))
    assert fetch_reliever_pitches(1, 2026) == []


def test_fetch_bullpen_never_pulls_statcast(monkeypatch):
    """One call per reliever against the two the rest of the category needs, for
    one key. It has to stay opt-in."""

    def boom(*args, **kwargs):
        raise AssertionError("fetch_bullpen must not pull Statcast")

    monkeypatch.setattr(
        "calculators.sources.category_07_bullpen_exposure._fetch_season_statcast", boom
    )

    def fake_get(url, params=None, **kwargs):
        if url.endswith("/roster"):
            return FakeResponse(_roster(_roster_entry(1)))
        return FakeResponse(_people(_person_log(1, [_log_split()])))

    monkeypatch.setattr(requests, "get", fake_get)
    assert len(fetch_bullpen(147, TODAY, 2026)) == 1
