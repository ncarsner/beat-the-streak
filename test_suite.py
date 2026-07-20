import pytest
import requests
from datetime import datetime, timedelta

import main
from main import (
    is_within_past_week,
    binomial_probability,
    lookup_player_info,
    scrape_player_data,
    compile_player_data,
    load_no_data_cache,
    save_no_data_cache,
    load_missing_team_cache,
    save_missing_team_cache,
    is_in_cooldown,
    resolve_run_config,
    fetch_schedule,
    fetch_lineup,
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


@pytest.fixture(autouse=True)
def clear_player_id_cache():
    main._player_id_cache.clear()
    yield
    main._player_id_cache.clear()


def _recent_date():
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _game(date_str, at_bats=3, hits=2, bb=1, so=0):
    return {
        "date": date_str,
        "stat": {"atBats": at_bats, "hits": hits, "baseOnBalls": bb, "strikeOuts": so},
    }


def _make_stats_get(stats_payload):
    """Fake requests.get that answers the id-lookup then the stats endpoint."""

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/people/search"):
            return FakeResponse(
                {"people": [{"id": 1, "currentTeam": {"name": "Test Team"}}]}
            )
        return FakeResponse(stats_payload)

    return fake_get


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


# ---- lookup_player_info ----


def test_lookup_player_info_found(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            {"people": [{"id": 42, "currentTeam": {"name": "Test Team"}}]}
        ),
    )
    result = lookup_player_info("Test Player")
    assert result == {"id": 42, "team_name": "Test Team"}


def test_lookup_player_info_no_current_team(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse({"people": [{"id": 42}]})
    )
    result = lookup_player_info("Test Player")
    assert result == {"id": 42, "team_name": None}


def test_lookup_player_info_not_found(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse({"people": []}))
    assert lookup_player_info("Nobody") is None


def test_lookup_player_info_caches_after_first_call(monkeypatch):
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return FakeResponse({"people": [{"id": 7, "currentTeam": {"name": "Team A"}}]})

    monkeypatch.setattr(requests, "get", fake_get)
    assert lookup_player_info("Cached Player") == {"id": 7, "team_name": "Team A"}
    assert lookup_player_info("Cached Player") == {"id": 7, "team_name": "Team A"}
    assert len(calls) == 1


def test_lookup_player_info_request_exception(monkeypatch):
    def fake_get(*a, **kw):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", fake_get)
    assert lookup_player_info("Whoever") is None


# ---- scrape_player_data ----
# Regression coverage for the stats-endpoint returning a present-but-empty
# "stats" list, which used to raise an uncaught IndexError before any
# guard existed around the JSON shape.


@pytest.mark.parametrize(
    "stats_payload",
    [
        {"stats": []},
        {"stats": [{}]},
        {"stats": [{"splits": []}]},
        {},
    ],
    ids=["empty-stats-list", "no-splits-key", "empty-splits", "no-stats-key"],
)
def test_scrape_player_data_handles_missing_or_empty_stats(monkeypatch, stats_payload):
    monkeypatch.setattr(requests, "get", _make_stats_get(stats_payload))
    assert scrape_player_data("Test Player", "unused") is None


def test_scrape_player_data_stale_last_game_returns_none(monkeypatch):
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    payload = {"stats": [{"splits": [_game(old_date)]}]}
    monkeypatch.setattr(requests, "get", _make_stats_get(payload))
    assert scrape_player_data("Test Player", "unused") is None


def test_scrape_player_data_success(monkeypatch):
    date_str = _recent_date()
    payload = {
        "stats": [
            {
                "splits": [
                    _game(date_str, at_bats=3, hits=2, bb=1, so=0),
                    _game(date_str, at_bats=4, hits=1, bb=0, so=1),
                    _game(date_str, at_bats=2, hits=0, bb=1, so=2),
                ]
            }
        ]
    }
    monkeypatch.setattr(requests, "get", _make_stats_get(payload))
    result = scrape_player_data("Test Player", "unused")
    assert result == {
        "Player": "Test Player",
        "Team": "",  # "Test Team" is not in TEAM_CROSSWALK
        "At Bats": 9,
        "Hits": 3,
        "Walks": 2,
        "Strikeouts": 3,
        "GameHourUTC": None,
    }


def test_scrape_player_data_no_player_id(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse({"people": []}))
    assert scrape_player_data("Unknown Player", "unused") is None


def test_scrape_player_data_request_exception(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        if url.endswith("/people/search"):
            return FakeResponse({"people": [{"id": 1}]})
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "get", fake_get)
    assert scrape_player_data("Test Player", "unused") is None


# ---- compile_player_data ----


@pytest.mark.parametrize("limit, expected_count", [(0, 0), (1, 1), (3, 3), (None, 5)])
def test_compile_player_data_respects_limit(monkeypatch, limit, expected_count):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    monkeypatch.setattr(
        main,
        "scrape_player_data",
        lambda player, url, missing_team_cache=None, schedule_map=None: {
            "Player": player,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        },
    )
    players = {f"Player{i}": f"p/player{i}.shtml" for i in range(5)}
    result = compile_player_data(players, limit=limit)
    assert len(result) == expected_count


def test_compile_player_data_skips_none_and_zero_at_bats(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)

    def fake_scrape(player, url, missing_team_cache=None, schedule_map=None):
        if player == "NoData":
            return None
        if player == "ZeroAtBats":
            return {
                "Player": player,
                "At Bats": 0,
                "Hits": 0,
                "Walks": 0,
                "Strikeouts": 0,
            }
        return {"Player": player, "At Bats": 10, "Hits": 5, "Walks": 1, "Strikeouts": 2}

    monkeypatch.setattr(main, "scrape_player_data", fake_scrape)
    players = {"NoData": "a", "ZeroAtBats": "b", "Active": "c"}
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
    cache = {"Player A": "2026-07-01", "Player B": "2026-07-05"}
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
        cache["Test Player"] = checked_date
    assert is_in_cooldown("Test Player", cache, cooldown_days) is expected


# ---- resolve_run_config ----


@pytest.mark.parametrize(
    "mode, expected_players, expected_limit",
    [
        ("subset", "selected_hitters", main.MAX_PLAYERS),
        ("max", "hitters", main.MAX_PLAYERS),
        ("full", "hitters", None),
    ],
)
def test_resolve_run_config(mode, expected_players, expected_limit):
    players, limit = resolve_run_config(mode)
    assert players is getattr(main, expected_players)
    assert limit == expected_limit


def test_resolve_run_config_invalid_mode_raises():
    with pytest.raises(ValueError):
        resolve_run_config("nonexistent-mode")


# ---- compile_player_data: cooldown-aware caching ----


def test_compile_player_data_skips_player_in_cooldown(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    scrape_calls = []

    def fake_scrape(player, url, missing_team_cache=None, schedule_map=None):
        scrape_calls.append(player)
        return {"Player": player, "At Bats": 10, "Hits": 5, "Walks": 1, "Strikeouts": 2}

    monkeypatch.setattr(main, "scrape_player_data", fake_scrape)

    recent = datetime.now().strftime("%Y-%m-%d")
    cache = {"OnCooldown": recent}
    players = {"OnCooldown": "a", "Fetchable": "b"}
    result = compile_player_data(players, limit=None, cooldown_days=7, cache=cache)

    assert scrape_calls == ["Fetchable"]
    assert [p["Player"] for p in result] == ["Fetchable"]
    assert cache == {
        "OnCooldown": recent
    }  # untouched: never scraped, still on cooldown


def test_compile_player_data_adds_player_to_cache_on_no_data(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    monkeypatch.setattr(
        main,
        "scrape_player_data",
        lambda player, url, missing_team_cache=None, schedule_map=None: None,
    )

    cache = {}
    players = {"NoData": "a"}
    compile_player_data(players, limit=None, cooldown_days=7, cache=cache)

    assert "NoData" in cache
    assert cache["NoData"] == datetime.now().strftime("%Y-%m-%d")


def test_compile_player_data_clears_cache_entry_on_success(monkeypatch):
    monkeypatch.setattr(main, "sleep", lambda _: None)
    monkeypatch.setattr(
        main,
        "scrape_player_data",
        lambda player, url, missing_team_cache=None, schedule_map=None: {
            "Player": player,
            "At Bats": 10,
            "Hits": 5,
            "Walks": 1,
            "Strikeouts": 2,
        },
    )

    stale_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    cache = {"Recovered": stale_date}
    players = {"Recovered": "a"}
    compile_player_data(players, limit=None, cooldown_days=7, cache=cache)

    assert "Recovered" not in cache


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


# ---- missing-team cache: crosswalk-miss logging ----


def _make_empty_stats_get(monkeypatch):
    """Patch requests.get so /stats returns no splits (scrape returns None quickly)."""

    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"stats": []})

    monkeypatch.setattr(requests, "get", fake_get)


def test_scrape_player_data_logs_crosswalk_miss(monkeypatch):
    """team_name present but not in crosswalk → entry written to missing_team_cache."""
    monkeypatch.setattr(
        main, "lookup_player_info", lambda name: {"id": 1, "team_name": "Unknown Team"}
    )
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Alice", "unused", missing)
    assert "Unknown Team" in missing
    assert missing["Unknown Team"]["players"] == ["Alice"]
    assert "first_seen" in missing["Unknown Team"]


def test_scrape_player_data_no_log_when_team_name_is_none(monkeypatch):
    """No currentTeam (team_name is None) → missing_team_cache untouched."""
    monkeypatch.setattr(
        main, "lookup_player_info", lambda name: {"id": 1, "team_name": None}
    )
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Bob", "unused", missing)
    assert missing == {}


def test_scrape_player_data_crosswalk_miss_deduplicates_player(monkeypatch):
    """Calling scrape twice for the same player/team does not duplicate the name."""
    monkeypatch.setattr(
        main, "lookup_player_info", lambda name: {"id": 1, "team_name": "Ghost Team"}
    )
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Carol", "unused", missing)
    scrape_player_data("Carol", "unused", missing)
    assert missing["Ghost Team"]["players"] == ["Carol"]


def test_scrape_player_data_crosswalk_miss_appends_different_players(monkeypatch):
    """Two different players with the same unknown team → both names listed."""
    monkeypatch.setattr(
        main, "lookup_player_info", lambda name: {"id": 1, "team_name": "Ghost Team"}
    )
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Dave", "unused", missing)
    scrape_player_data("Eve", "unused", missing)
    assert set(missing["Ghost Team"]["players"]) == {"Dave", "Eve"}


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


# ---- build_arg_parser ----


def test_build_arg_parser_defaults():
    args = build_arg_parser().parse_args([])
    assert args.mode == "subset"
    assert args.cooldown_days == DEFAULT_COOLDOWN_DAYS


def test_build_arg_parser_overrides():
    args = build_arg_parser().parse_args(["--mode", "full", "--cooldown-days", "3"])
    assert args.mode == "full"
    assert args.cooldown_days == 3
