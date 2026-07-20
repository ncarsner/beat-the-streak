import pytest
import requests
from datetime import datetime, timedelta

import main
from teams import TEAM_CROSSWALK, TEAM_ID_TO_ABBR
from main import (
    is_within_past_week,
    binomial_probability,
    compile_player_data,
    scrape_player_data,
    load_no_data_cache,
    save_no_data_cache,
    load_missing_team_cache,
    save_missing_team_cache,
    load_queried_games_cache,
    save_queried_games_cache,
    is_game_queried_today,
    process_game_lineup,
    is_in_cooldown,
    fetch_schedule,
    fetch_lineup,
    select_games,
    log_schedule_fetch_error,
    probable_hitters,
    build_arg_parser,
    DEFAULT_COOLDOWN_DAYS,
)


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


# ---- is_within_past_week ----


@pytest.mark.parametrize(
    "days_ago, expected",
    [
        (0, True),
        (1, True),
        (6, True),
        (7, False),
        (8, False),
        (30, False),
    ],
)
def test_is_within_past_week(days_ago, expected):
    date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    assert is_within_past_week(date_str) is expected


# ---- binomial_probability ----


@pytest.mark.parametrize(
    "ab, h, bb, expected",
    [
        (0, 0, 0, 0.0),
        (10, 5, 3, 1 - 0.5**2.6),
        (20, 10, 5, 1 - 0.5**5.0),
    ],
)
def test_binomial_probability(ab, h, bb, expected):
    assert binomial_probability(ab, h, bb) == pytest.approx(expected)


# ---- compile_player_data ----


def _make_player(name: str, pid: int = 1, team_id: int = 119) -> dict:
    return {"id": pid, "fullName": name, "team_id": team_id}


@pytest.mark.parametrize("limit, expected_count", [(0, 0), (1, 1), (3, 3), (None, 5)])
def test_compile_player_data_respects_limit(monkeypatch, limit, expected_count):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    monkeypatch.setattr(
        main,
        "scrape_player_data",
        lambda player_id, player_name, team_id, schedule_map=None: {
            "Player": player_name,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        },
    )
    players = [_make_player(f"Player{i}", pid=i) for i in range(5)]
    result = compile_player_data(players, limit=limit)
    assert len(result) == expected_count


def test_compile_player_data_skips_none_and_zero_at_bats(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)

    def fake_scrape(player_id, player_name, team_id, schedule_map=None):
        if player_name == "NoData":
            return None
        if player_name == "ZeroAtBats":
            return {
                "Player": player_name,
                "At Bats": 0,
                "Hits": 0,
                "Walks": 0,
                "Strikeouts": 0,
            }
        return {
            "Player": player_name,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        }

    monkeypatch.setattr(main, "scrape_player_data", fake_scrape)
    players = [
        _make_player("NoData", 1),
        _make_player("ZeroAtBats", 2),
        _make_player("Active", 3),
    ]
    result = compile_player_data(players, limit=None)
    assert [p["Player"] for p in result] == ["Active"]


# ---- no-data cache: persistence ----


def test_load_no_data_cache_missing_file_returns_empty_dict(tmp_path):
    assert load_no_data_cache(tmp_path / "does_not_exist.json") == {}


def test_load_no_data_cache_corrupt_file_returns_empty_dict(tmp_path):
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("not valid json")
    assert load_no_data_cache(bad_file) == {}


def test_save_and_load_no_data_cache_roundtrip(tmp_path):
    cache_file = tmp_path / "nested" / "no_data_cache.json"
    cache = {"664034": "2026-07-01", "592518": "2026-07-05"}
    save_no_data_cache(cache, cache_file)
    assert load_no_data_cache(cache_file) == cache


# ---- is_in_cooldown ----


@pytest.mark.parametrize(
    "days_since_checked, cooldown_days, expected",
    [
        (None, 7, False),  # never checked
        (0, 7, True),  # checked today
        (6, 7, True),  # inside the window
        (7, 7, False),  # exactly on the boundary
        (10, 7, False),  # outside the window
        (2, 3, True),  # custom shorter cooldown, still inside
        (2, 1, False),  # custom shorter cooldown, already outside
    ],
)
def test_is_in_cooldown(days_since_checked, cooldown_days, expected):
    cache = {}
    if days_since_checked is not None:
        checked_date = (datetime.now() - timedelta(days=days_since_checked)).strftime(
            "%Y-%m-%d"
        )
        cache["12345"] = checked_date
    assert is_in_cooldown(12345, cache, cooldown_days) is expected


# ---- compile_player_data: cooldown-aware caching ----


def test_compile_player_data_skips_player_in_cooldown(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    scrape_calls = []

    def fake_scrape(player_id, player_name, team_id, schedule_map=None):
        scrape_calls.append(player_name)
        return {
            "Player": player_name,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        }

    monkeypatch.setattr(main, "scrape_player_data", fake_scrape)

    recent = datetime.now().strftime("%Y-%m-%d")
    cache = {"1": recent}
    players = [_make_player("OnCooldown", 1), _make_player("Fetchable", 2)]
    result = compile_player_data(players, limit=None, cooldown_days=7, cache=cache)

    assert scrape_calls == ["Fetchable"]
    assert [p["Player"] for p in result] == ["Fetchable"]
    assert cache == {"1": recent}  # untouched: never scraped, still on cooldown


def test_compile_player_data_adds_player_to_cache_on_no_data(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    monkeypatch.setattr(
        main,
        "scrape_player_data",
        lambda player_id, player_name, team_id, schedule_map=None: None,
    )

    cache = {}
    players = [_make_player("NoData", 1)]
    compile_player_data(players, limit=None, cooldown_days=7, cache=cache)

    assert "1" in cache
    assert cache["1"] == datetime.now().strftime("%Y-%m-%d")


def test_compile_player_data_clears_cache_entry_on_success(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    monkeypatch.setattr(
        main,
        "scrape_player_data",
        lambda player_id, player_name, team_id, schedule_map=None: {
            "Player": player_name,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        },
    )

    stale_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cache = {"1": stale_date}
    players = [_make_player("Recovered", 1)]
    compile_player_data(players, limit=None, cooldown_days=7, cache=cache)

    assert "1" not in cache


# ---- missing-team cache: persistence ----


def test_load_missing_team_cache_missing_file_returns_empty_dict(tmp_path):
    assert load_missing_team_cache(tmp_path / "does_not_exist.json") == {}


def test_load_missing_team_cache_corrupt_file_returns_empty_dict(tmp_path):
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("not valid json")
    assert load_missing_team_cache(bad_file) == {}


def test_save_and_load_missing_team_cache_roundtrip(tmp_path):
    cache_file = tmp_path / "missing_team_cache.json"
    cache = {"Unknown FC": {"first_seen": "2026-07-13", "players": ["Alice"]}}
    save_missing_team_cache(cache, cache_file)
    assert load_missing_team_cache(cache_file) == cache


# ---- fetch_schedule ----


def _make_game(home_id, away_id, game_pk=700001, game_number=1, hour=19, state="Final"):
    return {
        "gamePk": game_pk,
        "gameNumber": game_number,
        "gameDate": f"2026-07-18T{hour:02d}:05:00Z",
        "status": {"detailedState": state},
        "teams": {
            "home": {"team": {"id": home_id}},
            "away": {"team": {"id": away_id}},
        },
    }


def _schedule_payload(games):
    return {"dates": [{"games": games}]}


def test_fetch_schedule_single_game(monkeypatch):
    game = _make_game(home_id=119, away_id=137, game_pk=700001, hour=19)
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_schedule_payload([game]))
    )
    result = fetch_schedule("2026-07-18")
    assert len(result) == 1
    r = result[0]
    assert r["gamePk"] == 700001
    assert r["gameNumber"] == 1
    assert r["home_team_id"] == 119
    assert r["away_team_id"] == 137
    assert r["start_dt"].hour == 19


def test_fetch_schedule_doubleheader_returns_both_games(monkeypatch):
    game1 = _make_game(home_id=119, away_id=137, game_pk=700001, game_number=1, hour=17)
    game2 = _make_game(home_id=119, away_id=137, game_pk=700002, game_number=2, hour=20)
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(_schedule_payload([game1, game2])),
    )
    result = fetch_schedule("2026-07-18")
    assert len(result) == 2
    assert {r["gamePk"] for r in result} == {700001, 700002}
    assert {r["gameNumber"] for r in result} == {1, 2}


def test_fetch_schedule_excludes_postponed(monkeypatch):
    game = _make_game(home_id=119, away_id=137, state="Postponed")
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_schedule_payload([game]))
    )
    assert fetch_schedule("2026-07-18") == []


@pytest.mark.parametrize(
    "payload",
    [{"dates": []}, {}],
    ids=["empty-dates", "absent-dates"],
)
def test_fetch_schedule_empty_or_absent_dates(monkeypatch, payload):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert fetch_schedule("2026-07-18") == []


# ---- fetch_lineup ----


def _make_boxscore_payload(home_order, away_order, home_team_id=119, away_team_id=137):
    """Build a fake boxscore payload with the given batting orders."""

    def _team(batting_order, team_id):
        players = {}
        for pid in batting_order:
            players[f"ID{pid}"] = {
                "person": {"id": pid, "fullName": f"Player {pid}"},
                "parentTeamId": team_id,
            }
        return {"battingOrder": batting_order, "players": players}

    return {
        "teams": {
            "home": _team(home_order, home_team_id),
            "away": _team(away_order, away_team_id),
        }
    }


def test_fetch_lineup_posted_both_teams(monkeypatch):
    payload = _make_boxscore_payload([111, 222], [333, 444])
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    result = fetch_lineup(700001)
    assert len(result["home"]) == 2
    assert len(result["away"]) == 2
    assert result["home"][0] == {"id": 111, "fullName": "Player 111", "team_id": 119}
    assert result["away"][0] == {"id": 333, "fullName": "Player 333", "team_id": 137}


@pytest.mark.parametrize(
    "home_order, away_order",
    [
        ([], []),
        ([], [111, 222]),
        ([111, 222], []),
    ],
    ids=["both-empty", "home-empty", "away-empty"],
)
def test_fetch_lineup_empty_batting_order_returns_empty_lists(
    monkeypatch, home_order, away_order
):
    payload = _make_boxscore_payload(home_order, away_order)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    result = fetch_lineup(700001)
    assert result["home"] == ([] if not home_order else result["home"])
    assert result["away"] == ([] if not away_order else result["away"])
    assert len(result["home"]) == len(home_order)
    assert len(result["away"]) == len(away_order)


def test_fetch_lineup_absent_batting_order_key(monkeypatch):
    payload = {"teams": {"home": {"players": {}}, "away": {"players": {}}}}
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    result = fetch_lineup(700001)
    assert result == {"home": [], "away": []}


def test_fetch_lineup_request_exception_returns_empty(monkeypatch):
    def fake_get(*a, **kw):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch_lineup(700001)
    assert result == {"home": [], "away": []}


def test_fetch_lineup_non_2xx_returns_empty(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse({}, status_code=503)
    )
    result = fetch_lineup(700001)
    assert result == {"home": [], "away": []}


# ---- log_schedule_fetch_error ----


def test_log_schedule_fetch_error_creates_dirs_and_appends(tmp_path):
    log_file = tmp_path / "subdir" / "errors.log"
    log_schedule_fetch_error("first error", path=log_file)
    assert log_file.exists()
    content = log_file.read_text()
    assert "first error" in content

    log_schedule_fetch_error("second error", path=log_file)
    content2 = log_file.read_text()
    assert "first error" in content2
    assert "second error" in content2
    assert content2.count("\n") == 2


# ---- probable_hitters ----


def _player_entry(name, prob, team="TST", at_bats=10, hits=3, walks=1, strikeouts=2):
    return {
        "Player": name,
        "Team": team,
        "probability": prob,
        "At Bats": at_bats,
        "Hits": hits,
        "Walks": walks,
        "Strikeouts": strikeouts,
    }


def test_probable_hitters_top_and_bottom_slices(capsys):
    data = [
        _player_entry("P1", 0.9),
        _player_entry("P2", 0.8),
        _player_entry("P3", 0.7),
        _player_entry("P4", 0.6),
        _player_entry("P5", 0.5),
        _player_entry("P6", 0.4),
        _player_entry("P7", 0.3),
        _player_entry("P8", 0.2),
    ]
    probable_hitters(data, n=2)
    out = capsys.readouterr().out
    # top n*2=4 players appear; bottom n=2 players appear
    assert "P1" in out
    assert "P4" in out
    assert "P7" in out
    assert "P8" in out
    # P5 and P6 are outside both slices
    assert "P5" not in out
    assert "P6" not in out


# ---- select_games ----


def _game_record(offset_minutes: float) -> dict:
    """Return a fake schedule record with start_dt = now + offset_minutes."""
    now = datetime(2026, 7, 20, 18, 0, 0)
    return {"gamePk": 1, "start_dt": now + timedelta(minutes=offset_minutes)}


_NOW = datetime(2026, 7, 20, 18, 0, 0)


def _gr(offset_minutes: float) -> dict:
    return {"gamePk": 1, "start_dt": _NOW + timedelta(minutes=offset_minutes)}


@pytest.mark.parametrize(
    "offset_minutes, expected_included",
    [
        (180, True),  # 3h in the future — included
        (-1, False),  # already started — excluded
        (0, True),  # exactly now — boundary: included (>= now)
        (90, True),  # 90 min in future — included
    ],
    ids=["3h-future", "already-started", "boundary-exactly-now", "90min-future"],
)
def test_select_games_manual_mode(offset_minutes, expected_included):
    game = _gr(offset_minutes)
    result = select_games([game], _NOW, scheduled=False)
    assert (game in result) is expected_included


@pytest.mark.parametrize(
    "offset_minutes, expected_included",
    [
        (90, True),  # 90 min — inside [0, 2h] window
        (180, False),  # 3h — outside window
        (-1, False),  # already started — excluded
        (0, False),  # exactly now — boundary: excluded (not > 0)
        (120, True),  # exactly 2h — boundary: included (<= 2h)
    ],
    ids=[
        "90min-in-window",
        "3h-outside-window",
        "already-started",
        "boundary-exactly-now",
        "boundary-exactly-2h",
    ],
)
def test_select_games_scheduled_mode(offset_minutes, expected_included):
    game = _gr(offset_minutes)
    result = select_games([game], _NOW, scheduled=True)
    assert (game in result) is expected_included


def test_select_games_empty_input():
    assert select_games([], _NOW, scheduled=False) == []
    assert select_games([], _NOW, scheduled=True) == []


def test_select_games_filters_multiple_games():
    games = [_gr(-60), _gr(60), _gr(90), _gr(200)]
    manual = select_games(games, _NOW, scheduled=False)
    assert len(manual) == 3  # excludes the -60 min game

    scheduled = select_games(games, _NOW, scheduled=True)
    assert len(scheduled) == 2  # 60 min and 90 min only; -60 and 200 excluded


# ---- build_arg_parser ----


def test_build_arg_parser_defaults():
    args = build_arg_parser().parse_args([])
    assert args.scheduled is False
    assert args.cooldown_days == DEFAULT_COOLDOWN_DAYS


def test_build_arg_parser_scheduled_flag():
    args = build_arg_parser().parse_args(["--scheduled"])
    assert args.scheduled is True


def test_build_arg_parser_cooldown_override():
    args = build_arg_parser().parse_args(["--cooldown-days", "3"])
    assert args.cooldown_days == 3


def test_build_arg_parser_mode_not_recognized():
    import pytest

    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--mode", "full"])


# ---- TEAM_ID_TO_ABBR ----


@pytest.mark.parametrize(
    "team_id, expected_abbr",
    [(info["id"], info["abbreviation"]) for info in TEAM_CROSSWALK.values()],
)
def test_team_id_to_abbr_all_30_teams(team_id, expected_abbr):
    assert TEAM_ID_TO_ABBR[team_id] == expected_abbr


# ---- scrape_player_data ----


def _make_stats_payload(splits: list[dict]) -> dict:
    return {"stats": [{"splits": splits}]}


def _make_split(
    date: str, at_bats: int = 3, hits: int = 1, walks: int = 0, strikeouts: int = 1
) -> dict:
    return {
        "date": date,
        "stat": {
            "atBats": at_bats,
            "hits": hits,
            "baseOnBalls": walks,
            "strikeOuts": strikeouts,
        },
    }


def _recent_date(days_ago: int = 1) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_scrape_player_data_returns_correct_shape(monkeypatch):
    splits = [
        _make_split(_recent_date(i), at_bats=4, hits=2, walks=1, strikeouts=1)
        for i in range(1, 4)
    ]
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_make_stats_payload(splits))
    )
    result = scrape_player_data(664034, "Freddie Freeman", 119)
    assert result is not None
    assert result["Player"] == "Freddie Freeman"
    assert result["Team"] == "LAD"
    assert result["At Bats"] == 12
    assert result["Hits"] == 6
    assert result["Walks"] == 3
    assert result["Strikeouts"] == 3
    assert result["GameHourUTC"] is None


def test_scrape_player_data_uses_schedule_map(monkeypatch):
    splits = [_make_split(_recent_date(1))]
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_make_stats_payload(splits))
    )
    result = scrape_player_data(664034, "Freddie Freeman", 119, schedule_map={119: 19})
    assert result is not None
    assert result["GameHourUTC"] == 19


def test_scrape_player_data_unknown_team_id_uses_fallback(monkeypatch):
    splits = [_make_split(_recent_date(1))]
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_make_stats_payload(splits))
    )
    result = scrape_player_data(999999, "Unknown Player", 9999)
    assert result is not None
    assert result["Team"] == "???"


def test_scrape_player_data_empty_stats_list_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse({"stats": []}))
    assert scrape_player_data(664034, "Freddie Freeman", 119) is None


def test_scrape_player_data_empty_splits_returns_none(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_make_stats_payload([]))
    )
    assert scrape_player_data(664034, "Freddie Freeman", 119) is None


def test_scrape_player_data_stale_last_game_returns_none(monkeypatch):
    stale_split = _make_split(
        (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    )
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(_make_stats_payload([stale_split])),
    )
    assert scrape_player_data(664034, "Freddie Freeman", 119) is None


def test_scrape_player_data_request_exception_returns_none(monkeypatch):
    def fake_get(*a, **kw):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(requests, "get", fake_get)
    assert scrape_player_data(664034, "Freddie Freeman", 119) is None


def test_scrape_player_data_non_2xx_returns_none(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse({}, status_code=500)
    )
    assert scrape_player_data(664034, "Freddie Freeman", 119) is None


def test_scrape_player_data_uses_last_5_splits(monkeypatch):
    # 7 splits, only last 5 should be counted
    splits = [_make_split(_recent_date(i), at_bats=1, hits=0) for i in range(7, 0, -1)]
    # Override last 5 to have hits
    for s in splits[-5:]:
        s["stat"]["hits"] = 1
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_make_stats_payload(splits))
    )
    result = scrape_player_data(664034, "Freddie Freeman", 119)
    assert result is not None
    assert result["At Bats"] == 5
    assert result["Hits"] == 5


# ---- queried-games cache: persistence ----


def test_load_queried_games_cache_missing_file_returns_empty_dict(tmp_path):
    assert load_queried_games_cache(tmp_path / "does_not_exist.json") == {}


def test_load_queried_games_cache_corrupt_file_returns_empty_dict(tmp_path):
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("not valid json")
    assert load_queried_games_cache(bad_file) == {}


def test_save_and_load_queried_games_cache_roundtrip(tmp_path):
    cache_file = tmp_path / "queried_games_cache.json"
    cache = {"700001": "2026-07-20", "700002": "2026-07-20"}
    save_queried_games_cache(cache, cache_file)
    assert load_queried_games_cache(cache_file) == cache


# ---- is_game_queried_today ----


def test_is_game_queried_today_absent_returns_false():
    assert is_game_queried_today(700001, {}, "2026-07-20") is False


def test_is_game_queried_today_present_with_today_returns_true():
    cache = {"700001": "2026-07-20"}
    assert is_game_queried_today(700001, cache, "2026-07-20") is True


def test_is_game_queried_today_stale_prior_day_returns_false():
    cache = {"700001": "2026-07-19"}
    assert is_game_queried_today(700001, cache, "2026-07-20") is False


# ---- process_game_lineup ----


def _make_schedule_game(game_pk=700001, home_team_id=119, away_team_id=137, hour=19):
    from datetime import timezone

    return {
        "gamePk": game_pk,
        "gameNumber": 1,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "start_dt": datetime(2026, 7, 20, hour, 0, 0, tzinfo=timezone.utc),
    }


def test_process_game_lineup_posted_marks_queried_and_adds_players(monkeypatch):
    home = [{"id": 111, "fullName": "Player 111", "team_id": 119}]
    away = [{"id": 222, "fullName": "Player 222", "team_id": 137}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": away})

    cache, schedule_map, all_players = {}, {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    assert result is True
    assert cache == {"700001": "2026-07-20"}
    assert all_players == home + away
    assert schedule_map[119] == 19
    assert schedule_map[137] == 19


def test_process_game_lineup_not_posted_does_not_mark_queried(monkeypatch):
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": [], "away": []})

    cache, schedule_map, all_players = {}, {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    assert result is True  # fetch was attempted
    assert cache == {}  # not posted → not marked
    assert all_players == []


def test_process_game_lineup_already_queried_today_skips_fetch(monkeypatch):
    fetch_calls = []

    def fake_fetch(pk):
        fetch_calls.append(pk)
        return {"home": [], "away": []}

    monkeypatch.setattr(main, "fetch_lineup", fake_fetch)

    cache = {"700001": "2026-07-20"}
    schedule_map, all_players = {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    assert result is False
    assert fetch_calls == []  # fetch_lineup was never called
    assert all_players == []


def test_process_game_lineup_prior_day_entry_does_not_block(monkeypatch):
    home = [{"id": 111, "fullName": "Player 111", "team_id": 119}]
    away = [{"id": 222, "fullName": "Player 222", "team_id": 137}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": away})

    cache = {"700001": "2026-07-19"}  # stale — yesterday
    schedule_map, all_players = {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    assert result is True
    assert cache == {"700001": "2026-07-20"}  # overwritten with today
    assert all_players == home + away
