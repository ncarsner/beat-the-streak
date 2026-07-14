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
    monkeypatch.setattr(main, "scrape_player_data", lambda player, url, missing_team_cache=None, schedule_map=None: None)

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
    monkeypatch.setattr(main, "lookup_player_info", lambda name: {"id": 1, "team_name": "Unknown Team"})
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Alice", "unused", missing)
    assert "Unknown Team" in missing
    assert missing["Unknown Team"]["players"] == ["Alice"]
    assert "first_seen" in missing["Unknown Team"]


def test_scrape_player_data_no_log_when_team_name_is_none(monkeypatch):
    """No currentTeam (team_name is None) → missing_team_cache untouched."""
    monkeypatch.setattr(main, "lookup_player_info", lambda name: {"id": 1, "team_name": None})
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Bob", "unused", missing)
    assert missing == {}


def test_scrape_player_data_crosswalk_miss_deduplicates_player(monkeypatch):
    """Calling scrape twice for the same player/team does not duplicate the name."""
    monkeypatch.setattr(main, "lookup_player_info", lambda name: {"id": 1, "team_name": "Ghost Team"})
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Carol", "unused", missing)
    scrape_player_data("Carol", "unused", missing)
    assert missing["Ghost Team"]["players"] == ["Carol"]


def test_scrape_player_data_crosswalk_miss_appends_different_players(monkeypatch):
    """Two different players with the same unknown team → both names listed."""
    monkeypatch.setattr(main, "lookup_player_info", lambda name: {"id": 1, "team_name": "Ghost Team"})
    _make_empty_stats_get(monkeypatch)

    missing = {}
    scrape_player_data("Dave", "unused", missing)
    scrape_player_data("Eve", "unused", missing)
    assert set(missing["Ghost Team"]["players"]) == {"Dave", "Eve"}
