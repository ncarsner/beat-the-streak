import json
import pytest
import requests
from datetime import datetime, timedelta

import main
from teams import TEAM_CROSSWALK, TEAM_ID_TO_ABBR
from main import (
    batting_order_spot,
    is_within_past_week,
    binomial_probability,
    compile_player_data,
    scrape_player_data,
    load_no_data_cache,
    save_no_data_cache,
    load_queried_games_cache,
    save_queried_games_cache,
    is_game_queried_today,
    load_sms_sent_cache,
    save_sms_sent_cache,
    is_sms_sent_today,
    process_game_lineup,
    is_in_cooldown,
    fetch_schedule,
    fetch_lineup,
    select_games,
    log_schedule_fetch_error,
    probable_hitters,
    group_picks_by_start_time,
    format_sms_body,
    send_sms_notification,
    dispatch_scheduled_sms,
    build_arg_parser,
    is_opener,
    opener_pitcher_ids,
    drop_opener_matchups,
    format_model,
    format_delta,
    model_delta,
    rank_key,
    probable_pitcher_id,
    refresh_opposing_pitchers,
    DEFAULT_COOLDOWN_DAYS,
)
from tests.conftest import FakeResponse


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
        # The real boxscore carries a per-player battingOrder as a 3-digit
        # string ("100" = spot 1). The fixture mirrors that so lineup_spot is
        # exercised by every test using this helper, not just the ones that
        # assert on it.
        for spot, pid in enumerate(batting_order, start=1):
            players[f"ID{pid}"] = {
                "person": {"id": pid, "fullName": f"Player {pid}"},
                "parentTeamId": team_id,
                "battingOrder": f"{spot}00",
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
    assert result["home"][0] == {
        "id": 111,
        "fullName": "Player 111",
        "team_id": 119,
        "lineup_spot": 1,
    }
    assert result["away"][0] == {
        "id": 333,
        "fullName": "Player 333",
        "team_id": 137,
        "lineup_spot": 1,
    }


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
    cache = {
        "700001": {
            "date": "2026-07-20",
            "players": [{"id": 1, "fullName": "A", "team_id": 119}],
        },
        "700002": {"date": "2026-07-20", "players": []},
    }
    save_queried_games_cache(cache, cache_file)
    assert load_queried_games_cache(cache_file) == cache


def test_queried_games_cache_roundtrips_what_process_game_lineup_writes(
    monkeypatch, tmp_path
):
    """Round-trip the producer's real payload, not a hand-built stand-in.

    The test above invents `{"id": 1, "fullName": "A", "team_id": 119}`, a shape
    `process_game_lineup` has never written. That is why it stayed green while
    every real run crashed: the real payload carries `start_dt`, a `datetime`,
    which `json.dump` cannot serialize. Driving the producer keeps this test
    honest the next time a field is added to the context.
    """
    home = [{"id": 111, "fullName": "Player 111", "team_id": 119}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": []})

    cache, schedule_map, all_players = {}, {}, []
    process_game_lineup(
        _make_schedule_game(), cache, "2026-07-20", schedule_map, all_players
    )

    cache_file = tmp_path / "queried_games_cache.json"
    save_queried_games_cache(cache, cache_file)
    restored = load_queried_games_cache(cache_file)

    assert restored == cache
    start = restored["700001"]["players"][0]["start_dt"]
    assert isinstance(start, datetime)
    # Naive would not fail here, it would silently move CALC_70's answer.
    assert start.tzinfo is not None
    assert start.utcoffset() == timedelta(0)


def test_load_queried_games_cache_unparseable_start_dt_returns_empty_dict(tmp_path):
    cache_file = tmp_path / "queried_games_cache.json"
    cache_file.write_text(
        json.dumps(
            {"700001": {"date": "2026-07-20", "players": [{"start_dt": "not-a-time"}]}}
        )
    )
    assert load_queried_games_cache(cache_file) == {}


def test_load_queried_games_cache_tolerates_players_predating_start_dt(tmp_path):
    """A record written before the model fields existed must still load."""
    cache_file = tmp_path / "queried_games_cache.json"
    cache = {"700001": {"date": "2026-07-20", "players": [{"id": 1, "team_id": 119}]}}
    cache_file.write_text(json.dumps(cache))
    assert load_queried_games_cache(cache_file) == cache


# ---- is_game_queried_today ----


def test_is_game_queried_today_absent_returns_false():
    assert is_game_queried_today(700001, {}, "2026-07-20") is False


def test_is_game_queried_today_present_with_today_returns_true():
    cache = {"700001": {"date": "2026-07-20", "players": []}}
    assert is_game_queried_today(700001, cache, "2026-07-20") is True


def test_is_game_queried_today_stale_prior_day_returns_false():
    cache = {"700001": {"date": "2026-07-19", "players": []}}
    assert is_game_queried_today(700001, cache, "2026-07-20") is False


# ---- process_game_lineup ----


def _make_schedule_game(
    game_pk=700001,
    home_team_id=119,
    away_team_id=137,
    hour=19,
    home_pitcher_id=543037,
    away_pitcher_id=554430,
):
    from datetime import timezone

    return {
        "gamePk": game_pk,
        "gameNumber": 1,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "start_dt": datetime(2026, 7, 20, hour, 0, 0, tzinfo=timezone.utc),
        "home_pitcher_id": home_pitcher_id,
        "away_pitcher_id": away_pitcher_id,
    }


def _with_opponent(players, pitcher_id):
    """A cached player record: only the opposing starter is re-resolved."""
    return [{**p, "opposing_pitcher_id": pitcher_id} for p in players]


def _posted(players, pitcher_id, side):
    """What `process_game_lineup` records for a freshly posted lineup.

    Carries the game context the model needs alongside the opposing starter.
    Those fields are additive: nothing in the ranking path reads them, and a
    cache entry written before they existed still works because every consumer
    reaches for them with `.get`.
    """
    return [
        {
            **p,
            "game_pk": 700001,
            "game_number": 1,
            "start_dt": _make_schedule_game()["start_dt"],
            "opposing_pitcher_id": pitcher_id,
            "side": side,
            "is_home": side == "home",
        }
        for p in players
    ]


def test_process_game_lineup_posted_marks_queried_and_adds_players(monkeypatch):
    home = [{"id": 111, "fullName": "Player 111", "team_id": 119}]
    away = [{"id": 222, "fullName": "Player 222", "team_id": 137}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": away})

    cache, schedule_map, all_players = {}, {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    expected = _posted(home, 554430, "home") + _posted(away, 543037, "away")
    assert result is True
    assert cache == {"700001": {"date": "2026-07-20", "players": expected}}
    assert all_players == expected
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


def test_process_game_lineup_already_queried_today_skips_fetch_but_reuses_players(
    monkeypatch,
):
    fetch_calls = []

    def fake_fetch(pk):
        fetch_calls.append(pk)
        return {"home": [], "away": []}

    monkeypatch.setattr(main, "fetch_lineup", fake_fetch)

    cached_players = [{"id": 111, "fullName": "Player 111", "team_id": 119}]
    cache = {"700001": {"date": "2026-07-20", "players": cached_players}}
    schedule_map, all_players = {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    assert result is False
    assert fetch_calls == []  # fetch_lineup was never called
    # cached players still surface in output, with their opponent re-resolved
    assert all_players == _with_opponent(cached_players, 554430)
    assert schedule_map[119] == 19
    assert schedule_map[137] == 19


def test_process_game_lineup_prior_day_entry_does_not_block(monkeypatch):
    home = [{"id": 111, "fullName": "Player 111", "team_id": 119}]
    away = [{"id": 222, "fullName": "Player 222", "team_id": 137}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": away})

    stale_players = [{"id": 999, "fullName": "Stale Player", "team_id": 119}]
    cache = {
        "700001": {"date": "2026-07-19", "players": stale_players}
    }  # stale — yesterday
    schedule_map, all_players = {}, []
    g = _make_schedule_game()
    result = process_game_lineup(g, cache, "2026-07-20", schedule_map, all_players)

    expected = _posted(home, 554430, "home") + _posted(away, 543037, "away")
    assert result is True
    assert cache == {
        "700001": {"date": "2026-07-20", "players": expected}
    }  # overwritten with today
    assert all_players == expected  # fresh fetch, not the stale cached players


# ---- group_picks_by_start_time ----


def _summary_entry(name, prob, game_hour=19, team="TST"):
    return {
        "Player": name,
        "Team": team,
        "probability": prob,
        "At Bats": 10,
        "Hits": 3,
        "Walks": 1,
        "Strikeouts": 2,
        "GameHourUTC": game_hour,
    }


@pytest.mark.parametrize(
    "hours, expected_keys",
    [
        ([18, 18, 21, 21], {18, 21}),  # two distinct groupings
        ([19, 19, 19, 19, 19, 19], {19}),  # single grouping
        ([17, 20, 23], {17, 20, 23}),  # three distinct groupings
    ],
    ids=["two-groupings", "single-grouping", "three-groupings"],
)
def test_group_picks_grouping_keys(hours, expected_keys):
    data = [
        _summary_entry(f"P{i}", 0.9 - i * 0.05, game_hour=h)
        for i, h in enumerate(hours)
    ]
    result = group_picks_by_start_time(data)
    assert set(result.keys()) == expected_keys


def test_group_picks_two_groupings_ranked_independently():
    data = [
        _summary_entry("A", 0.7, game_hour=18),
        _summary_entry("B", 0.6, game_hour=18),
        _summary_entry("C", 0.9, game_hour=21),
        _summary_entry("D", 0.5, game_hour=21),
    ]
    result = group_picks_by_start_time(data)
    assert [p["Player"] for p in result[18]] == ["A", "B"]
    assert [p["Player"] for p in result[21]] == ["C", "D"]


@pytest.mark.parametrize(
    "player_count, top_n, expected_count",
    [
        (3, 5, 3),  # fewer than top_n — return all, no padding
        (5, 5, 5),  # exactly top_n
        (7, 5, 5),  # more than top_n — sliced to top_n
        (2, 5, 2),  # well below minimum — return all
    ],
    ids=["fewer-than-top_n", "exactly-top_n", "more-than-top_n", "two-players"],
)
def test_group_picks_respects_top_n_floor_and_cap(player_count, top_n, expected_count):
    data = [
        _summary_entry(f"P{i}", 0.9 - i * 0.05, game_hour=19)
        for i in range(player_count)
    ]
    result = group_picks_by_start_time(data, top_n=top_n)
    assert len(result[19]) == expected_count


def test_group_picks_sorted_by_probability_descending():
    data = [
        _summary_entry("Low", 0.3, game_hour=20),
        _summary_entry("High", 0.9, game_hour=20),
        _summary_entry("Mid", 0.6, game_hour=20),
    ]
    result = group_picks_by_start_time(data)
    probs = [p["probability"] for p in result[20]]
    assert probs == sorted(probs, reverse=True)


def test_group_picks_excludes_none_game_hour():
    data = [
        _summary_entry("HasHour", 0.8, game_hour=19),
        {**_summary_entry("NoHour", 0.9), "GameHourUTC": None},
    ]
    result = group_picks_by_start_time(data)
    assert set(result.keys()) == {19}
    assert all(p["Player"] != "NoHour" for p in result[19])


def test_group_picks_empty_input_returns_empty_dict():
    assert group_picks_by_start_time([]) == {}


def test_group_picks_does_not_modify_probable_hitters(capsys):
    # probable_hitters global behavior unchanged after adding group_picks_by_start_time
    data = [_player_entry(f"P{i}", 0.9 - i * 0.1) for i in range(8)]
    probable_hitters(data, n=2)
    out = capsys.readouterr().out
    assert "P1" in out  # top slice present
    assert "P8" not in out  # out-of-range player absent


# ---- format_sms_body ----


def _ranked_entry(name, prob, team="TST"):
    return {"Player": name, "Team": team, "probability": prob}


def test_format_sms_body_header_contains_game_hour():
    body = format_sms_body([_ranked_entry("P1", 0.683)], game_hour_utc=19)
    assert "19:00 UTC" in body


def test_format_sms_body_numbered_lines():
    ranked = [
        _ranked_entry("Freddie Freeman", 0.683, "LAD"),
        _ranked_entry("Max Muncy", 0.551, "LAD"),
    ]
    body = format_sms_body(ranked, game_hour_utc=19)
    lines = body.splitlines()
    assert lines[1].startswith("1.")
    assert "Freddie Freeman" in lines[1]
    assert "LAD" in lines[1]
    assert "68.3%" in lines[1]
    assert lines[2].startswith("2.")
    assert "Max Muncy" in lines[2]
    assert "55.1%" in lines[2]


def test_format_sms_body_zero_pads_hour():
    body = format_sms_body([_ranked_entry("P1", 0.5)], game_hour_utc=9)
    assert "09:00 UTC" in body


# ---- send_sms_notification ----

_CREDS = {
    "account_sid": "ACtest123",
    "auth_token": "tokenabc",
    "from_number": "+15550001111",
    "to_number": "+15559998888",
}


def test_send_sms_notification_success(monkeypatch):
    calls = []

    def fake_post(url, auth=None, data=None, timeout=None):
        calls.append({"url": url, "auth": auth, "data": data})
        return FakeResponse({"sid": "SM123"}, status_code=201)

    monkeypatch.setattr(requests, "post", fake_post)
    ranked = [_ranked_entry("P1", 0.7, "NYY")]
    result = send_sms_notification(ranked, 19, **_CREDS)

    assert result is True
    assert len(calls) == 1
    assert "ACtest123" in calls[0]["url"]
    assert calls[0]["auth"] == ("ACtest123", "tokenabc")
    assert calls[0]["data"]["From"] == "+15550001111"
    assert calls[0]["data"]["To"] == "+15559998888"
    assert "19:00 UTC" in calls[0]["data"]["Body"]


def test_send_sms_notification_non_2xx_returns_false(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: FakeResponse({"message": "bad request"}, status_code=400),
    )
    result = send_sms_notification([_ranked_entry("P1", 0.7)], 19, **_CREDS)
    assert result is False


def test_send_sms_notification_request_exception_returns_false(monkeypatch):
    def fake_post(*a, **kw):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(requests, "post", fake_post)
    result = send_sms_notification([_ranked_entry("P1", 0.7)], 19, **_CREDS)
    assert result is False


def test_send_sms_notification_does_not_raise_on_failure(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: FakeResponse({}, status_code=500),
    )
    try:
        send_sms_notification([_ranked_entry("P1", 0.7)], 19, **_CREDS)
    except Exception as exc:
        pytest.fail(f"send_sms_notification raised unexpectedly: {exc}")


# ---- dispatch_scheduled_sms ----


def _set_twilio_env(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    monkeypatch.setenv("SUBSCRIBER_PHONE_NUMBER", "+15559998888")


def test_dispatch_scheduled_sms_calls_send_per_grouping(monkeypatch):
    calls = []

    def fake_send(ranked, game_hour, *args, **kwargs):
        calls.append(game_hour)
        return True

    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(main, "send_sms_notification", fake_send)
    summary = [
        _summary_entry("P1", 0.8, game_hour=18),
        _summary_entry("P2", 0.7, game_hour=19),
    ]
    dispatch_scheduled_sms(summary, {}, "2026-07-24")
    assert sorted(calls) == [18, 19]


def test_dispatch_scheduled_sms_empty_summary_no_send(monkeypatch):
    calls = []
    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(
        main, "send_sms_notification", lambda *a, **kw: calls.append(True)
    )
    dispatch_scheduled_sms([], {}, "2026-07-24")
    assert calls == []


# ---- sms_sent_cache: persistence ----


def test_load_sms_sent_cache_missing_file_returns_empty_dict(tmp_path):
    assert load_sms_sent_cache(tmp_path / "does_not_exist.json") == {}


def test_load_sms_sent_cache_corrupt_file_returns_empty_dict(tmp_path):
    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("not valid json")
    assert load_sms_sent_cache(bad_file) == {}


def test_save_and_load_sms_sent_cache_roundtrip(tmp_path):
    cache_file = tmp_path / "sms_sent_cache.json"
    cache = {"19": "2026-07-24", "23": "2026-07-24"}
    save_sms_sent_cache(cache, cache_file)
    assert load_sms_sent_cache(cache_file) == cache


# ---- is_sms_sent_today ----


def test_is_sms_sent_today_absent_returns_false():
    assert is_sms_sent_today(19, {}, "2026-07-24") is False


def test_is_sms_sent_today_present_with_today_returns_true():
    cache = {"19": "2026-07-24"}
    assert is_sms_sent_today(19, cache, "2026-07-24") is True


def test_is_sms_sent_today_stale_prior_day_returns_false():
    cache = {"19": "2026-07-23"}
    assert is_sms_sent_today(19, cache, "2026-07-24") is False


# ---- dispatch_scheduled_sms: cache-aware behavior ----


def test_dispatch_marks_cache_on_successful_send(monkeypatch):
    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(main, "send_sms_notification", lambda *a, **kw: True)
    cache: dict = {}
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    dispatch_scheduled_sms(summary, cache, "2026-07-24")
    assert cache == {"19": "2026-07-24"}


def test_dispatch_skips_already_sent_grouping(monkeypatch):
    send_calls = []
    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(
        main,
        "send_sms_notification",
        lambda *a, **kw: send_calls.append(True) or True,
    )
    cache = {"19": "2026-07-24"}
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    dispatch_scheduled_sms(summary, cache, "2026-07-24")
    assert send_calls == []


def test_dispatch_does_not_mark_cache_on_failed_send(monkeypatch):
    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(main, "send_sms_notification", lambda *a, **kw: False)
    cache: dict = {}
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    dispatch_scheduled_sms(summary, cache, "2026-07-24")
    assert cache == {}


def test_dispatch_prior_day_cache_entry_does_not_block_today(monkeypatch):
    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(main, "send_sms_notification", lambda *a, **kw: True)
    cache = {"19": "2026-07-23"}  # stale — yesterday
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    dispatch_scheduled_sms(summary, cache, "2026-07-24")
    assert cache == {"19": "2026-07-24"}


@pytest.mark.parametrize(
    "missing_var",
    [
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
        "SUBSCRIBER_PHONE_NUMBER",
    ],
)
def test_dispatch_missing_credential_logs_and_skips_sms(
    monkeypatch, missing_var, capsys
):
    all_vars = {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "tok",
        "TWILIO_FROM_NUMBER": "+15550001111",
        "SUBSCRIBER_PHONE_NUMBER": "+15559998888",
    }
    send_calls = []
    monkeypatch.setattr(
        main,
        "send_sms_notification",
        lambda *a, **kw: send_calls.append(True) or True,
    )
    for var, val in all_vars.items():
        if var == missing_var:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    dispatch_scheduled_sms(summary, {}, "2026-07-24")
    assert send_calls == []
    assert "SMS send skipped" in capsys.readouterr().out


def test_dispatch_all_credentials_set_proceeds_to_send(monkeypatch):
    send_calls = []
    _set_twilio_env(monkeypatch)
    monkeypatch.setattr(
        main,
        "send_sms_notification",
        lambda *a, **kw: send_calls.append(True) or True,
    )
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    dispatch_scheduled_sms(summary, {}, "2026-07-24")
    assert len(send_calls) == 1


# ---- run: scheduled gate ----


def _fake_args(
    scheduled=False,
    cooldown_days=DEFAULT_COOLDOWN_DAYS,
    exclude_openers=False,
    model=False,
):
    import argparse

    return argparse.Namespace(
        scheduled=scheduled,
        cooldown_days=cooldown_days,
        exclude_openers=exclude_openers,
        model=model,
    )


def _patch_run_io(monkeypatch, summary_data):
    """Stub all I/O in run() so tests touch neither disk nor network."""
    monkeypatch.setattr(main, "fetch_schedule", lambda date: [])
    monkeypatch.setattr(main, "load_queried_games_cache", lambda: {})
    monkeypatch.setattr(main, "load_no_data_cache", lambda: {})
    monkeypatch.setattr(main, "load_sms_sent_cache", lambda: {})
    monkeypatch.setattr(main, "save_no_data_cache", lambda cache: None)
    monkeypatch.setattr(main, "save_queried_games_cache", lambda cache: None)
    monkeypatch.setattr(main, "save_sms_sent_cache", lambda cache: None)
    monkeypatch.setattr(main, "compile_player_data", lambda **kw: summary_data)
    monkeypatch.setattr(main, "probable_hitters", lambda data, n=5: None)


def test_run_manual_mode_does_not_send_sms_even_with_players(monkeypatch):
    sms_calls = []
    summary = [_summary_entry("P1", 0.8, game_hour=19)]
    _patch_run_io(monkeypatch, summary)
    monkeypatch.setattr(
        main, "send_sms_notification", lambda *a, **kw: sms_calls.append(True)
    )
    main.run(_fake_args(scheduled=False))
    assert sms_calls == []


def test_run_scheduled_mode_sends_sms_for_each_grouping(monkeypatch):
    sms_calls = []
    summary = [
        _summary_entry("P1", 0.8, game_hour=18),
        _summary_entry("P2", 0.7, game_hour=19),
    ]
    _patch_run_io(monkeypatch, summary)
    monkeypatch.setattr(
        main,
        "send_sms_notification",
        lambda ranked, hour, *a, **kw: sms_calls.append(hour) or True,
    )
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+10005551111")
    monkeypatch.setenv("SUBSCRIBER_PHONE_NUMBER", "+10005552222")
    main.run(_fake_args(scheduled=True))
    assert sorted(sms_calls) == [18, 19]


# ---- probable pitcher plumbing ----


def _make_game_with_pitchers(home_pitcher=None, away_pitcher=None, **kw):
    kw.setdefault("home_id", 119)
    kw.setdefault("away_id", 137)
    game = _make_game(**kw)
    if home_pitcher is not None:
        game["teams"]["home"]["probablePitcher"] = {"id": home_pitcher}
    if away_pitcher is not None:
        game["teams"]["away"]["probablePitcher"] = {"id": away_pitcher}
    return game


@pytest.mark.parametrize(
    "game, side, expected",
    [
        ({"teams": {"home": {"probablePitcher": {"id": 543037}}}}, "home", 543037),
        ({"teams": {"home": {}}}, "home", None),
        ({"teams": {"home": {"probablePitcher": None}}}, "home", None),
        ({"teams": {}}, "away", None),
        ({}, "away", None),
    ],
)
def test_probable_pitcher_id(game, side, expected):
    assert probable_pitcher_id(game, side) == expected


def test_fetch_schedule_carries_probable_pitcher_ids(monkeypatch):
    game = _make_game_with_pitchers(home_pitcher=543037, away_pitcher=554430)
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse(_schedule_payload([game]))
    )
    result = fetch_schedule("2026-07-18")[0]
    assert result["home_pitcher_id"] == 543037
    assert result["away_pitcher_id"] == 554430


def test_fetch_schedule_unannounced_starters_are_none(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            _schedule_payload([_make_game(home_id=119, away_id=137)])
        ),
    )
    result = fetch_schedule("2026-07-18")[0]
    assert result["home_pitcher_id"] is None
    assert result["away_pitcher_id"] is None


def test_fetch_schedule_requests_probable_pitcher_hydration(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(params or {})
        return FakeResponse(_schedule_payload([]))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_schedule("2026-07-18")
    assert captured["hydrate"] == "probablePitcher"


def test_process_game_lineup_assigns_the_opposing_side_starter(monkeypatch):
    home = [{"id": 111, "fullName": "Home Bat", "team_id": 119}]
    away = [{"id": 222, "fullName": "Away Bat", "team_id": 137}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": away})

    all_players = []
    g = _make_schedule_game(home_pitcher_id=543037, away_pitcher_id=554430)
    process_game_lineup(g, {}, "2026-07-20", {}, all_players)

    by_name = {p["fullName"]: p["opposing_pitcher_id"] for p in all_players}
    assert by_name == {"Home Bat": 554430, "Away Bat": 543037}


def test_process_game_lineup_unannounced_starter_leaves_opponent_none(monkeypatch):
    home = [{"id": 111, "fullName": "Home Bat", "team_id": 119}]
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": home, "away": []})

    all_players = []
    g = _make_schedule_game(home_pitcher_id=None, away_pitcher_id=None)
    process_game_lineup(g, {}, "2026-07-20", {}, all_players)

    assert all_players[0]["opposing_pitcher_id"] is None


# ---- refresh_opposing_pitchers: probables announced after the lineup posts ----


def test_refresh_opposing_pitchers_resolves_by_team():
    players = [
        {"id": 111, "team_id": 119, "opposing_pitcher_id": None},
        {"id": 222, "team_id": 137, "opposing_pitcher_id": None},
    ]
    g = _make_schedule_game(home_pitcher_id=543037, away_pitcher_id=554430)
    refreshed = refresh_opposing_pitchers(players, g)
    assert [p["opposing_pitcher_id"] for p in refreshed] == [554430, 543037]


def test_refresh_opposing_pitchers_keeps_cached_value_for_unknown_team():
    players = [{"id": 111, "team_id": 999, "opposing_pitcher_id": 660271}]
    g = _make_schedule_game(home_pitcher_id=543037, away_pitcher_id=554430)
    assert refresh_opposing_pitchers(players, g)[0]["opposing_pitcher_id"] == 660271


def test_refresh_opposing_pitchers_leaves_none_while_starter_unannounced():
    players = [{"id": 111, "team_id": 119}]
    g = _make_schedule_game(home_pitcher_id=None, away_pitcher_id=None)
    assert refresh_opposing_pitchers(players, g)[0]["opposing_pitcher_id"] is None


def test_refresh_opposing_pitchers_does_not_mutate_input():
    players = [{"id": 111, "team_id": 119, "opposing_pitcher_id": None}]
    g = _make_schedule_game(home_pitcher_id=543037, away_pitcher_id=554430)
    refresh_opposing_pitchers(players, g)
    assert players[0]["opposing_pitcher_id"] is None


def test_process_game_lineup_cached_entry_picks_up_a_late_announced_starter(
    monkeypatch,
):
    """A lineup can post before the probable does; the cached None must not stick."""
    fetch_calls = []
    monkeypatch.setattr(
        main,
        "fetch_lineup",
        lambda pk: fetch_calls.append(pk) or {"home": [], "away": []},
    )

    cached_players = [
        {"id": 111, "fullName": "Home Bat", "team_id": 119, "opposing_pitcher_id": None}
    ]
    cache = {"700001": {"date": "2026-07-20", "players": cached_players}}
    all_players = []
    g = _make_schedule_game(home_pitcher_id=543037, away_pitcher_id=554430)
    result = process_game_lineup(g, cache, "2026-07-20", {}, all_players)

    assert result is False
    assert fetch_calls == []  # still no redundant lineup fetch
    assert all_players[0]["opposing_pitcher_id"] == 554430
    # the cache converges too, so the fix survives a save/load round trip
    assert cache["700001"]["players"][0]["opposing_pitcher_id"] == 554430


def test_process_game_lineup_cached_entry_predating_the_field_is_backfilled(
    monkeypatch,
):
    monkeypatch.setattr(main, "fetch_lineup", lambda pk: {"home": [], "away": []})

    cached_players = [{"id": 222, "fullName": "Away Bat", "team_id": 137}]
    cache = {"700001": {"date": "2026-07-20", "players": cached_players}}
    all_players = []
    g = _make_schedule_game(home_pitcher_id=543037, away_pitcher_id=554430)
    process_game_lineup(g, cache, "2026-07-20", {}, all_players)

    assert all_players[0]["opposing_pitcher_id"] == 543037


# ---- batting_order_spot: the 3-digit boxscore encoding ----


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100", 1),
        ("200", 2),
        ("900", 9),
        ("101", 1),  # first substitute in the leadoff spot
        ("903", 9),  # third substitute in the ninth spot
        (100, 1),  # already an int
    ],
)
def test_batting_order_spot_decodes_the_three_digit_encoding(raw, expected):
    """ "101" is a substitute batting first, not spot 101 and not spot 2."""
    assert batting_order_spot(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "1000", -100, {}, []])
def test_batting_order_spot_rejects_unusable_values(raw):
    """Missing, non-numeric, or out-of-range degrades to None rather than raising."""
    assert batting_order_spot(raw) is None


def test_fetch_lineup_carries_the_lineup_spot(monkeypatch):
    """Each lineup entry gains lineup_spot, additively."""
    payload = {
        "teams": {
            "home": {
                "battingOrder": [111, 222],
                "players": {
                    "ID111": {
                        "person": {"fullName": "Leadoff Guy"},
                        "parentTeamId": 147,
                        "battingOrder": "100",
                    },
                    "ID222": {
                        "person": {"fullName": "Cleanup Guy"},
                        "parentTeamId": 147,
                        "battingOrder": "400",
                    },
                },
            },
            "away": {},
        }
    }
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    home = fetch_lineup(777)["home"]

    assert [p["lineup_spot"] for p in home] == [1, 4]
    assert home[0]["fullName"] == "Leadoff Guy"
    assert home[0]["id"] == 111


def test_fetch_lineup_missing_batting_order_field_yields_none_spot(monkeypatch):
    """A player object with no battingOrder key still produces an entry."""
    payload = {
        "teams": {
            "home": {
                "battingOrder": [111],
                "players": {
                    "ID111": {"person": {"fullName": "No Spot"}, "parentTeamId": 147}
                },
            },
            "away": {},
        }
    }
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    entry = fetch_lineup(777)["home"][0]

    assert entry["lineup_spot"] is None
    assert entry["fullName"] == "No Spot"


def test_binomial_probability_still_uses_the_pa_over_five_exponent():
    """CALC_41 is built but NOT wired: the run's math is unchanged.

    If this ever fails, the ranked table's numbers moved, and that is a decision
    that has to be made deliberately rather than as a side effect.
    """
    import inspect

    source = inspect.getsource(binomial_probability)
    assert "pa / 5" in source
    # And the value itself: 20 AB, 6 H, 5 BB -> pa=25, exp=5, avg=.3
    assert binomial_probability(20, 6, 5) == pytest.approx(1 - (1 - 0.3) ** 5)


# ---- opener exclusion ----


def _log(bf=4, pitches=15, started=False, game_date="2026-08-08"):
    from tests.conftest import _relief_line

    return _relief_line(game_date=game_date, bf=bf, pitches=pitches, started=started)


def test_a_conventional_starter_is_not_an_opener():
    assert is_opener([_log(bf=24, started=True) for _ in range(5)]) is False


def test_a_short_average_start_is_an_opener():
    assert is_opener([_log(bf=5, started=True) for _ in range(4)]) is True


def test_a_reliever_making_his_first_start_is_an_opener():
    """CALC_51 returns None here because there is no start length to measure.
    As a measurement that is correct; as a decision it is the clearest opener
    there is, which is why the gate carries its own rule."""
    assert is_opener([_log(bf=4) for _ in range(40)]) is True


def test_a_starter_between_assignments_is_not_an_opener():
    """Mostly starting work and no short starts. The relief rule must not fire
    on a rotation arm who has taken a piggyback outing."""
    games = [_log(bf=24, started=True) for _ in range(10)] + [_log(bf=6)]
    assert is_opener(games) is False


def test_an_unknown_pitcher_is_treated_as_a_conventional_starter():
    """Include-when-unknown. Dropping a playable matchup over a missing history
    is the worse failure."""
    assert is_opener([]) is False


def test_openers_are_resolved_in_one_request(monkeypatch):
    calls = []

    def fake_logs(ids, season):
        calls.append(sorted(ids))
        return {pid: [_log(bf=5, started=True)] for pid in ids}

    monkeypatch.setattr(main, "fetch_pitching_game_logs", fake_logs)
    games = [
        {"home_pitcher_id": 1, "away_pitcher_id": 2},
        {"home_pitcher_id": 3, "away_pitcher_id": 4},
    ]
    assert opener_pitcher_ids(games, 2026) == {1, 2, 3, 4}
    assert calls == [[1, 2, 3, 4]]


def test_an_unannounced_starter_contributes_no_id(monkeypatch):
    monkeypatch.setattr(
        main, "fetch_pitching_game_logs", lambda ids, season: {i: [] for i in ids}
    )
    games = [{"home_pitcher_id": None, "away_pitcher_id": 7}]
    assert opener_pitcher_ids(games, 2026) == set()


def test_a_slate_with_no_announced_starters_makes_no_request(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not have been called")

    monkeypatch.setattr(main, "fetch_pitching_game_logs", boom)
    assert opener_pitcher_ids([{"home_pitcher_id": None}], 2026) == set()


def test_only_the_side_facing_the_opener_is_dropped():
    """A club using an opener does not stop the other club from starting a
    conventional pitcher, so dropping the whole game would discard nine hitters
    facing exactly what this tool ranks."""
    players = [
        {"fullName": "Faces opener", "opposing_pitcher_id": 1},
        {"fullName": "Faces starter", "opposing_pitcher_id": 2},
    ]
    kept = drop_opener_matchups(players, {1})
    assert [p["fullName"] for p in kept] == ["Faces starter"]


def test_a_hitter_with_an_unannounced_opposing_starter_is_kept():
    players = [{"fullName": "Unknown matchup", "opposing_pitcher_id": None}]
    assert drop_opener_matchups(players, {1}) == players


def test_no_openers_leaves_the_pool_untouched():
    players = [{"opposing_pitcher_id": 1}]
    assert drop_opener_matchups(players, set()) is players


def test_the_flag_defaults_to_excluding_openers():
    assert build_arg_parser().parse_args([]).exclude_openers is True


def test_the_flag_can_be_turned_off():
    assert (
        build_arg_parser().parse_args(["--no-exclude-openers"]).exclude_openers is False
    )


def test_the_flag_can_be_stated_explicitly():
    assert build_arg_parser().parse_args(["--exclude-openers"]).exclude_openers is True


def test_the_flag_is_independent_of_scheduled_mode():
    args = build_arg_parser().parse_args(["--scheduled", "--no-exclude-openers"])
    assert args.scheduled is True
    assert args.exclude_openers is False


# ---- Model column ----


def test_the_model_column_renders_a_percentage():
    assert format_model(0.7239) == "72.4%"


def test_an_unresolved_model_renders_a_dash():
    """A dash, not a blank or a zero: the model returning nothing is a different
    statement from it returning a low probability."""
    assert format_model(None) == "-"


def test_the_model_flag_defaults_to_unset():
    """Unset rather than True, because run() resolves it differently for a
    manual run and a scheduled one."""
    assert build_arg_parser().parse_args([]).model is None


def test_the_model_flag_can_be_forced_on():
    assert build_arg_parser().parse_args(["--model"]).model is True


def test_the_model_flag_can_be_forced_off():
    assert build_arg_parser().parse_args(["--no-model"]).model is False


def test_the_table_carries_a_model_column(monkeypatch, capsys):
    rows = [
        {
            "Player": "A",
            "Team": "NYY",
            "Hits": 5,
            "At Bats": 10,
            "Walks": 1,
            "Strikeouts": 2,
            "probability": 0.9,
            "model": 0.72,
        }
    ]
    probable_hitters(rows, n=1)
    out = capsys.readouterr().out
    assert "Model" in out
    assert "72.0%" in out


def test_a_player_without_a_model_value_still_renders(capsys):
    """The model is additive. A run that did not compute it, or a hitter it
    could not resolve, must not break the table."""
    rows = [
        {
            "Player": "A",
            "Team": "NYY",
            "Hits": 5,
            "At Bats": 10,
            "Walks": 1,
            "Strikeouts": 2,
            "probability": 0.9,
        }
    ]
    probable_hitters(rows, n=1)
    assert "-" in capsys.readouterr().out


def test_no_model_context_means_no_model_key(monkeypatch):
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
    result = compile_player_data([_make_player("A", 1)], limit=None)
    assert "model" not in result[0]


def test_the_model_is_computed_only_for_players_that_produce_data(monkeypatch):
    """A model evaluation costs a Statcast pull for anyone not already cached
    today, so it must not run for a hitter who is skipped anyway."""
    monkeypatch.setattr(main, "sleep", lambda _: None)
    evaluated = []
    monkeypatch.setattr(
        main,
        "player_model_probability",
        lambda player, context: evaluated.append(player["id"]) or 0.5,
    )

    def fake_scrape(player_id, player_name, team_id, schedule_map=None):
        if player_name == "NoData":
            return None
        return {
            "Player": player_name,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        }

    monkeypatch.setattr(main, "scrape_player_data", fake_scrape)
    compile_player_data(
        [_make_player("NoData", 1), _make_player("Good", 2)],
        limit=None,
        model_context={"schedule": {}, "lineups": {}},
    )
    assert evaluated == [2]


def test_a_failing_model_evaluation_does_not_take_down_the_run(monkeypatch):
    """Additive column. One hitter whose Statcast pull times out must not stop
    the tool from producing a ranking."""

    def boom(*args, **kwargs):
        raise RuntimeError("savant down")

    monkeypatch.setattr("calculators.pipeline.assemble", boom)
    player = {"id": 1, "fullName": "A", "game_pk": 700001}
    context = {"schedule": {}, "lineups": {}, "today": None, "season": 2026}
    assert main.player_model_probability(player, context) is None


# ---- Delta column and agreement ordering ----


def _row(name, probability, model=None):
    return {
        "Player": name,
        "Team": "NYY",
        "Hits": 5,
        "At Bats": 10,
        "Walks": 1,
        "Strikeouts": 2,
        "probability": probability,
        **({"model": model} if model is not None else {}),
    }


def test_the_delta_is_model_minus_the_heuristic():
    assert model_delta(_row("A", 0.50, 0.725)) == pytest.approx(0.225)


def test_the_delta_is_signed_so_direction_survives():
    """Two methods disagreeing by 20 points in opposite directions are different
    findings, and an absolute value would collapse them."""
    assert model_delta(_row("A", 0.80, 0.60)) < 0
    assert model_delta(_row("B", 0.60, 0.80)) > 0


def test_an_unresolved_model_has_no_delta():
    """Not a zero delta. A hitter the model could not evaluate has no
    disagreement to report."""
    assert model_delta(_row("A", 0.50)) is None


@pytest.mark.parametrize(
    "value, rendered", [(0.225, "+22.5"), (-0.18, "-18.0"), (0.0, "+0.0"), (None, "-")]
)
def test_the_delta_renders_with_a_sign(value, rendered):
    assert format_delta(value) == rendered


def test_probability_is_the_primary_sort():
    rows = [_row("low", 0.40, 0.41), _row("high", 0.90, 0.20)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)] == ["high", "low"]


def test_a_large_disagreement_does_not_outrank_a_higher_probability():
    """Delta breaks ties. It does not re-rank hitters the heuristic separates,
    which would need a weighting nobody has measured."""
    rows = [_row("high", 0.80, 0.79), _row("lower", 0.70, 0.95)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)][0] == "high"


def test_the_lowest_delta_leads_among_equal_probabilities():
    """The stated top of the table: highest probability, lowest model delta."""
    rows = [_row("up", 0.75, 0.90), _row("down", 0.75, 0.60)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)] == ["down", "up"]


def test_the_highest_positive_delta_trails_among_equal_probabilities():
    """The stated bottom of the table: lowest probability, highest positive
    delta. Same key, read from the other end."""
    rows = [_row("worst", 0.30, 0.80), _row("agrees", 0.30, 0.31)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)][-1] == "worst"


def test_the_secondary_key_is_signed_not_absolute():
    """An absolute key collapses a model that disagrees upward with one that
    disagrees downward, and the two ends of the table depend on telling them
    apart."""
    rows = [_row("over", 0.50, 0.70), _row("under", 0.50, 0.30)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)] == ["under", "over"]


def test_rows_without_a_model_sort_after_resolved_rows_at_the_same_probability():
    """Not a zero delta: nothing is known about the model's opinion, so he
    cannot be placed between a disagreement and an agreement."""
    rows = [_row("none", 0.50), _row("resolved", 0.50, 0.90)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)] == ["resolved", "none"]


def test_an_unmodelled_row_still_sorts_by_probability():
    rows = [_row("low", 0.20), _row("high", 0.80)]
    assert [r["Player"] for r in sorted(rows, key=rank_key)] == ["high", "low"]


def test_the_table_carries_a_delta_column(capsys):
    probable_hitters([_row("A", 0.50, 0.725)], n=1)
    out = capsys.readouterr().out
    assert "Delta" in out
    assert "+22.5" in out


def test_the_table_leads_with_the_highest_probability(capsys):
    rows = [_row("mid", 0.70, 0.62), _row("best", 0.90, 0.55), _row("next", 0.80, 0.81)]
    probable_hitters(rows, n=2)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "|" in ln]
    order = [n for ln in lines for n in ("best", "next", "mid") if f" {n} " in ln]
    assert order[:3] == ["best", "next", "mid"]


def test_selection_still_runs_off_the_heuristic(capsys):
    """Which hitters appear is unchanged. Only the order within a section moved."""
    rows = [_row(f"P{i}", 0.9 - i / 100, 0.5) for i in range(20)]
    probable_hitters(rows, n=2)
    out = capsys.readouterr().out
    assert "P0" in out  # highest probability, still shown
    assert "P19" in out  # lowest probability, still shown
    assert "P10" not in out  # middle of the pack, still omitted


def test_a_table_without_any_model_still_renders(capsys):
    """With --no-model every delta is None, so the sort is inert and the table
    keeps its probability order."""
    probable_hitters([_row("A", 0.9), _row("B", 0.5)], n=1)
    assert "Delta" in capsys.readouterr().out
