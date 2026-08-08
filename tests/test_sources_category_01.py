import pytest
import requests
from datetime import datetime

from calculators import sources
from calculators.category_01_bvp_matchups import parse_bvp_stats
from calculators.sources import (
    attach_category_01,
    fetch_bvp_stats,
    fetch_bvp_statcast,
)
from tests.conftest import (
    FakeResponse,
    _split,
    _vsplayer_payload,
    _pitch,
    _install_fake_pybaseball,
    _statcast_frame,
)


# ---- fetch_bvp_stats ----


def test_fetch_bvp_stats_parses_response(monkeypatch):
    payload = _vsplayer_payload(
        [_split(season=2021, pa=70, ab=60, h=21), _split(season=2026, pa=6, ab=6, h=2)]
    )
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse(payload))
    bvp = fetch_bvp_stats(592450, 543037)
    assert bvp["career"]["hits"] == 23
    assert bvp["by_season"][2026]["plateAppearances"] == 6


def test_fetch_bvp_stats_sends_opposing_player_id(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured.update(params or {})
        return FakeResponse({"stats": []})

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_bvp_stats(592450, 543037)
    assert captured["url"].endswith("/people/592450/stats")
    assert captured["opposingPlayerId"] == 543037
    assert captured["stats"] == "vsPlayer"
    assert captured["group"] == "hitting"


def test_fetch_bvp_stats_request_exception_returns_empty_payload(monkeypatch, capsys):
    def boom(*a, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_bvp_stats(592450, 543037) == {"career": None, "by_season": {}}
    assert "BvP fetch error" in capsys.readouterr().out


def test_fetch_bvp_stats_non_2xx_returns_empty_payload(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResponse({}, 503))
    assert fetch_bvp_stats(592450, 543037) == {"career": None, "by_season": {}}


# ---- attach_category_01 ----


def test_attach_category_01_adds_all_four_keys(monkeypatch):
    monkeypatch.setattr(
        sources,
        "fetch_bvp_stats",
        lambda b, p: parse_bvp_stats(
            _vsplayer_payload([_split(season=2026, pa=6, ab=6, h=2, so=1, bb=0)])
        ),
    )
    # attach_category_01 fetches from two sources; patching only the Stats API
    # half leaves the Statcast pull live, which is how this test spent its first
    # life quietly scraping Savant on every run.
    monkeypatch.setattr(sources, "fetch_bvp_statcast", lambda b, p, s: [])
    data = {"Player": "Someone"}
    attach_category_01(data, 592450, 543037, season=2026)
    assert data["CALC_01"].value == pytest.approx((2 / 6, 6))
    assert data["CALC_02"].value == pytest.approx((2 / 6, 6))
    assert data["CALC_03"].value == pytest.approx((2 / 6, 6))
    assert data["CALC_04"].value == pytest.approx((5 / 6, 6))


def test_attach_category_01_skips_fetch_without_a_starter(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sources, "fetch_bvp_stats", lambda b, p: calls.append((b, p)) or {}
    )
    data = {"Player": "Someone"}
    attach_category_01(data, 592450, None, season=2026)
    assert calls == []
    assert data["CALC_01"] is None
    assert data["CALC_04"] is None


def test_attach_category_01_defaults_season_to_current_year(monkeypatch):
    seasons = []
    monkeypatch.setattr(
        sources, "fetch_bvp_stats", lambda b, p: {"career": None, "by_season": {}}
    )
    monkeypatch.setattr(sources, "fetch_bvp_statcast", lambda b, p, s: [])
    monkeypatch.setattr(
        sources,
        "compute_category_01",
        lambda bvp, season, pitches=None: seasons.append(season) or {},
    )
    attach_category_01({}, 592450, 543037)
    assert seasons == [datetime.today().year]


# ---- fetch_bvp_statcast ----


def test_fetch_bvp_statcast_filters_to_the_matchup(monkeypatch):
    frame = _statcast_frame(
        [
            {"pitcher": 554430, "description": "foul", "strikes": 1, "events": None},
            {"pitcher": 999999, "description": "ball", "strikes": 0, "events": None},
        ]
    )
    _install_fake_pybaseball(monkeypatch, frame=frame)
    result = fetch_bvp_statcast(518692, 554430, season=2026)
    assert len(result) == 1
    assert result[0]["description"] == "foul"


def test_fetch_bvp_statcast_converts_nan_to_none(monkeypatch):
    frame = _statcast_frame(
        [
            {
                "pitcher": 554430,
                "description": "hit_into_play",
                "strikes": 0,
                "events": "single",
                "launch_speed": float("nan"),
                "estimated_ba_using_speedangle": 0.4,
            }
        ]
    )
    _install_fake_pybaseball(monkeypatch, frame=frame)
    record = fetch_bvp_statcast(518692, 554430, season=2026)[0]
    assert record["launch_speed"] is None
    assert record["estimated_ba_using_speedangle"] == pytest.approx(0.4)


def test_fetch_bvp_statcast_requests_the_requested_season(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    fetch_bvp_statcast(518692, 554430, season=2019)
    start, end, player_id = calls[0]
    assert start == "2019-01-01"
    assert end == "2019-12-31"
    assert player_id == 518692


def test_fetch_bvp_statcast_caps_the_end_date_at_today(monkeypatch):
    from datetime import date

    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    fetch_bvp_statcast(518692, 554430, season=date.today().year)
    assert calls[0][1] == date.today().isoformat()


def test_fetch_bvp_statcast_before_statcast_existed_returns_empty(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    assert fetch_bvp_statcast(518692, 554430, season=2014) == []
    assert calls == []  # no pointless request


def test_fetch_bvp_statcast_fetch_failure_returns_empty(monkeypatch, capsys):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant down"))
    assert fetch_bvp_statcast(518692, 554430, season=2026) == []
    assert "Statcast fetch failed" in capsys.readouterr().out


def test_fetch_bvp_statcast_empty_frame_returns_empty(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    assert fetch_bvp_statcast(518692, 554430, season=2026) == []


def test_fetch_bvp_statcast_frame_without_pitcher_column_returns_empty(monkeypatch):
    _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame([{"description": "ball"}])
    )
    assert fetch_bvp_statcast(518692, 554430, season=2026) == []


def test_attach_category_01_pulls_statcast_when_a_starter_is_known(monkeypatch):
    monkeypatch.setattr(sources, "fetch_bvp_stats", lambda b, p: sources.empty_bvp())
    seen = []
    monkeypatch.setattr(
        sources,
        "fetch_bvp_statcast",
        lambda b, p, s: seen.append((b, p, s)) or [_pitch("swinging_strike")],
    )
    data = {}
    attach_category_01(data, 518692, 554430, season=2026)
    assert seen == [(518692, 554430, 2026)]
    assert data["CALC_07"].value == pytest.approx((1.0, 1))


def test_attach_category_01_skips_statcast_without_a_starter(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "fetch_bvp_statcast", lambda *a: calls.append(a) or [])
    data = {}
    attach_category_01(data, 518692, None, season=2026)
    assert calls == []
    assert data["CALC_05"] is None


def test_fetch_bvp_statcast_converts_pandas_na_to_none(monkeypatch):
    """`events` is unset on most pitches and arrives as pd.NA, not NaN."""
    import pandas as pd

    frame = pd.DataFrame(
        {
            "pitcher": [554430],
            "description": pd.array(["foul"], dtype="string"),
            "events": pd.array([None], dtype="string"),
            "strikes": [1],
        }
    )
    _install_fake_pybaseball(monkeypatch, frame=frame)
    record = fetch_bvp_statcast(518692, 554430, season=2026)[0]
    assert record["events"] is None
    assert record["description"] == "foul"


def test_fetch_bvp_statcast_record_carries_no_pandas_sentinels(monkeypatch):
    import pandas as pd

    frame = pd.DataFrame(
        {
            "pitcher": [554430],
            "description": pd.array(["hit_into_play"], dtype="string"),
            "events": pd.array([None], dtype="string"),
            "strikes": [2],
            "launch_speed": [float("nan")],
            "estimated_ba_using_speedangle": [pd.NA],
        }
    )
    _install_fake_pybaseball(monkeypatch, frame=frame)
    record = fetch_bvp_statcast(518692, 554430, season=2026)[0]
    for key, value in record.items():
        assert value is None or not pd.isna(value), f"{key} leaked {value!r}"
