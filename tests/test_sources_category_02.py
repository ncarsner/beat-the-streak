"""Tests for calculators/sources/category_02_platoon_splits.py.

All tests mock requests.get — no live API call is ever made.
"""

import requests

from calculators.category_02_platoon_splits import _plate_appearances
from calculators.sources.category_01_bvp_matchups import fetch_bvp_statcast
from calculators.sources.category_02_platoon_splits import (
    _parse_stat_splits,
    empty_stat_splits,
    fetch_batter_statcast,
    fetch_handedness,
    fetch_pitcher_statcast,
    fetch_stat_splits,
)
from tests.conftest import (
    FakeResponse,
    _install_fake_pybaseball,
    _statcast_frame,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _people_payload(*people):
    """Build a minimal /people?personIds=... response."""
    return {"people": list(people)}


def _person(pid, bats=None, throws=None):
    """Build one person object as the MLB Stats API returns it."""
    obj = {"id": pid}
    if bats is not None:
        obj["batSide"] = {"code": bats}
    if throws is not None:
        obj["pitchHand"] = {"code": throws}
    return obj


# ---------------------------------------------------------------------------
# fetch_handedness — multiple ids in one request
# ---------------------------------------------------------------------------


def test_fetch_handedness_one_request_for_multiple_ids(monkeypatch):
    """N ids cost exactly one GET, not N."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        payload = _people_payload(
            _person(592450, bats="L", throws="R"),
            _person(543037, bats="R", throws="R"),
        )
        return FakeResponse(payload)

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch_handedness([592450, 543037])

    assert len(calls) == 1
    assert result[592450] == {"bats": "L", "throws": "R"}
    assert result[543037] == {"bats": "R", "throws": "R"}


def test_fetch_handedness_sends_comma_separated_ids(monkeypatch):
    """The personIds param joins all requested ids in one string."""
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update({"url": url, "params": params or {}})
        return FakeResponse(_people_payload())

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_handedness([111, 222, 333])

    assert captured["url"].endswith("/people")
    # All three ids must appear in the personIds param value.
    ids_sent = set(captured["params"]["personIds"].split(","))
    assert ids_sent == {"111", "222", "333"}


# ---------------------------------------------------------------------------
# fetch_handedness — switch hitter
# ---------------------------------------------------------------------------


def test_fetch_handedness_identifies_switch_hitter(monkeypatch):
    """A switch hitter has batSide.code == 'S'."""
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            _people_payload(_person(123456, bats="S", throws="R"))
        ),
    )
    result = fetch_handedness([123456])
    assert result[123456]["bats"] == "S"


# ---------------------------------------------------------------------------
# fetch_handedness — id absent from response
# ---------------------------------------------------------------------------


def test_fetch_handedness_absent_id_not_in_result(monkeypatch):
    """An id the API does not return is absent from the mapping (caller uses .get())."""
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            _people_payload(_person(592450, bats="L", throws="R"))
        ),
    )
    result = fetch_handedness([592450, 999999])

    assert 592450 in result
    assert 999999 not in result
    assert result.get(999999) is None


# ---------------------------------------------------------------------------
# fetch_handedness — request failure
# ---------------------------------------------------------------------------


def test_fetch_handedness_request_exception_returns_empty(monkeypatch, capsys):
    """A network error returns {} and prints a message — no raise."""

    def boom(*a, **kw):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(requests, "get", boom)
    result = fetch_handedness([592450, 543037])

    assert result == {}
    assert "Handedness fetch error" in capsys.readouterr().out


def test_fetch_handedness_non_2xx_returns_empty(monkeypatch):
    """A non-2xx response returns {} rather than raising."""
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse({}, status_code=503)
    )
    assert fetch_handedness([592450]) == {}


# ---------------------------------------------------------------------------
# fetch_handedness — edge cases
# ---------------------------------------------------------------------------


def test_fetch_handedness_empty_ids_makes_no_request(monkeypatch):
    """An empty collection returns {} without issuing any request."""
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: calls.append(1) or FakeResponse({})
    )
    result = fetch_handedness([])

    assert result == {}
    assert calls == []  # no pointless request


def test_fetch_handedness_response_with_no_people_key(monkeypatch):
    """A response body that lacks 'people' normalizes to empty rather than raising."""
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse({"something_else": []})
    )
    assert fetch_handedness([592450]) == {}


def test_fetch_handedness_missing_bat_side_degrades_to_none(monkeypatch):
    """A person record without batSide yields bats=None rather than raising."""
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            _people_payload(_person(592450, throws="R"))  # no bats kwarg
        ),
    )
    result = fetch_handedness([592450])
    assert result[592450]["bats"] is None
    assert result[592450]["throws"] == "R"


# ---------------------------------------------------------------------------
# statSplits helpers
# ---------------------------------------------------------------------------


def _stat_split(code, **stat):
    """Build one statSplits split object as the MLB Stats API returns it."""
    return {"split": {"code": code}, "stat": stat}


def _stat_splits_payload(*splits):
    """Build a raw stats=statSplits response wrapping *splits*."""
    return {"stats": [{"splits": list(splits)}]}


# ---------------------------------------------------------------------------
# _parse_stat_splits — the battersFaced regression
# ---------------------------------------------------------------------------


def test_parse_stat_splits_pitching_uses_batters_faced_as_pa():
    """The regression this normalization exists to prevent.

    Pitching splits carry no plateAppearances key at all. Parsed naively the
    line's PA is 0, rate_or_none sees a zero denominator, and CALC_11 returns
    None for every pitcher on the slate — silently, with no error anywhere.
    Values are Wheeler's real 2026 vs-LHB line, verified against the live API.
    """
    payload = _stat_splits_payload(
        _stat_split(
            "vl", battersFaced=261, atBats=236, hits=49, strikeOuts=78, baseOnBalls=22
        )
    )
    parsed = _parse_stat_splits(payload, "pitching")

    assert parsed["vl"]["plateAppearances"] == 261
    assert parsed["vl"]["atBats"] == 236
    assert parsed["vl"]["hits"] == 49


def test_parse_stat_splits_hitting_keeps_plate_appearances():
    """The hitting group already carries plateAppearances; no substitution."""
    payload = _stat_splits_payload(
        _stat_split(
            "vr",
            plateAppearances=328,
            atBats=287,
            hits=94,
            strikeOuts=49,
            baseOnBalls=37,
        )
    )
    parsed = _parse_stat_splits(payload, "hitting")

    assert parsed["vr"]["plateAppearances"] == 328
    assert parsed["vr"]["hits"] == 94


def test_parse_stat_splits_hitting_does_not_read_batters_faced():
    """A hitting line never sources its PA from battersFaced, even if present."""
    payload = _stat_splits_payload(
        _stat_split("vl", plateAppearances=157, battersFaced=999, atBats=140, hits=37)
    )
    assert _parse_stat_splits(payload, "hitting")["vl"]["plateAppearances"] == 157


def test_parse_stat_splits_keys_both_codes():
    """vl and vr are returned separately, keyed by split code."""
    payload = _stat_splits_payload(
        _stat_split("vl", plateAppearances=157, atBats=140, hits=37),
        _stat_split("vr", plateAppearances=328, atBats=287, hits=94),
    )
    parsed = _parse_stat_splits(payload, "hitting")

    assert set(parsed) == {"vl", "vr"}
    assert parsed["vl"]["hits"] == 37
    assert parsed["vr"]["hits"] == 94


def test_parse_stat_splits_ignores_unrequested_codes():
    """Codes other than vl/vr are dropped rather than polluting the mapping."""
    payload = _stat_splits_payload(
        _stat_split("vl", plateAppearances=157, hits=37),
        _stat_split("vlg", plateAppearances=999, hits=999),
    )
    assert set(_parse_stat_splits(payload, "hitting")) == {"vl"}


def test_parse_stat_splits_fills_missing_counting_stats_with_zero():
    """A split missing a counting stat yields 0 for it, not a KeyError."""
    parsed = _parse_stat_splits(
        _stat_splits_payload(_stat_split("vl", hits=3)), "hitting"
    )
    assert parsed["vl"]["hits"] == 3
    assert parsed["vl"]["strikeOuts"] == 0
    assert parsed["vl"]["plateAppearances"] == 0


def test_parse_stat_splits_null_stat_value_becomes_zero():
    """An explicit null from the API is coerced to 0, matching parse_bvp_stats."""
    parsed = _parse_stat_splits(
        _stat_splits_payload(_stat_split("vl", plateAppearances=None, hits=3)),
        "hitting",
    )
    assert parsed["vl"]["plateAppearances"] == 0


def test_parse_stat_splits_malformed_payloads_normalize_to_empty():
    """Malformed input returns {} rather than raising, like parse_bvp_stats."""
    for payload in ({}, {"stats": None}, {"stats": []}, {"stats": [{"splits": None}]}):
        assert _parse_stat_splits(payload, "hitting") == {}


def test_empty_stat_splits_is_an_empty_mapping():
    """The absent-lookup sentinel is a fresh empty mapping."""
    assert empty_stat_splits() == {}
    # A function, not a shared constant: one caller's edit must not leak.
    first = empty_stat_splits()
    first["vl"] = {"hits": 1}
    assert empty_stat_splits() == {}


# ---------------------------------------------------------------------------
# fetch_stat_splits
# ---------------------------------------------------------------------------


def test_fetch_stat_splits_sends_expected_params(monkeypatch):
    """One request, carrying the stat type, both sit codes, group and season."""
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update({"url": url, "params": params or {}})
        return FakeResponse(_stat_splits_payload())

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_stat_splits(554430, "pitching", 2026)

    assert captured["url"].endswith("/people/554430/stats")
    assert captured["params"]["stats"] == "statSplits"
    assert captured["params"]["group"] == "pitching"
    assert set(captured["params"]["sitCodes"].split(",")) == {"vl", "vr"}
    assert captured["params"]["season"] == 2026


def test_fetch_stat_splits_parses_pitching_response(monkeypatch):
    """End to end: a pitching response comes back with PA sourced from battersFaced."""
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: FakeResponse(
            _stat_splits_payload(
                _stat_split("vl", battersFaced=261, atBats=236, hits=49)
            )
        ),
    )
    assert fetch_stat_splits(554430, "pitching", 2026)["vl"]["plateAppearances"] == 261


def test_fetch_stat_splits_request_exception_returns_empty(monkeypatch, capsys):
    """A network error returns the empty payload and prints — no raise."""

    def boom(*a, **kw):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_stat_splits(554430, "pitching", 2026) == empty_stat_splits()
    assert capsys.readouterr().out != ""


def test_fetch_stat_splits_non_2xx_returns_empty(monkeypatch):
    """A non-2xx response returns the empty payload rather than raising."""
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: FakeResponse({}, status_code=503)
    )
    assert fetch_stat_splits(518692, "hitting", 2026) == empty_stat_splits()


# ---------------------------------------------------------------------------
# Statcast fetchers
# ---------------------------------------------------------------------------


def _sc_row(**overrides):
    """Build one Statcast frame row carrying the Category 2 fields."""
    row = {
        "game_date": "2026-08-01",
        "game_pk": 777001,
        "at_bat_number": 12,
        "events": None,
        "description": "ball",
        "stand": "L",
        "p_throws": "R",
        "arm_angle": 37.0,
        "estimated_ba_using_speedangle": None,
        "estimated_woba_using_speedangle": None,
    }
    row.update(overrides)
    return row


def test_fetch_pitcher_statcast_normalizes_rows(monkeypatch):
    """Rows come back as plain dicts carrying the Category 2 field set."""
    _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame([_sc_row(), _sc_row(stand="R")])
    )
    records = fetch_pitcher_statcast(554430, season=2026)

    assert len(records) == 2
    assert [r["stand"] for r in records] == ["L", "R"]
    assert records[0]["p_throws"] == "R"
    assert records[0]["arm_angle"] == 37.0


def test_fetch_pitcher_statcast_uses_the_pitcher_entry_point(monkeypatch):
    """The pitcher fetcher must pull statcast_pitcher, not statcast_batter.

    The two share a cache directory keyed by role; pulling the wrong entry point
    would quietly file one side's frame under the other's name.
    """
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame([_sc_row()])
    )
    fetch_pitcher_statcast(554430, season=2026)
    assert calls[0][0] == "pitcher"
    assert calls[0][-1] == 554430


def test_fetch_batter_statcast_uses_the_batter_entry_point(monkeypatch):
    """The batter fetcher must pull statcast_batter."""
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([_sc_row()]))
    fetch_batter_statcast(518692, season=2026)
    assert calls[0][0] != "pitcher"
    assert calls[0][-1] == 518692


def test_fetch_batter_statcast_is_not_filtered_to_one_pitcher(monkeypatch):
    """Unlike fetch_bvp_statcast, every pitch the batter saw is returned.

    CALC_10 and CALC_14 average over a class of pitcher, not one matchup, so
    filtering here would destroy the population they are defined over.
    """
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(
            [_sc_row(p_throws="R"), _sc_row(p_throws="L"), _sc_row(p_throws="R")]
        ),
    )
    records = fetch_batter_statcast(518692, season=2026)
    assert len(records) == 3
    assert {r["p_throws"] for r in records} == {"L", "R"}


def test_statcast_fetchers_leak_no_pandas_sentinels(monkeypatch):
    """No np.nan or pd.NA reaches a calculator, in either direction."""
    import numpy as np
    import pandas as pd

    frame = _statcast_frame([_sc_row(), _sc_row()])
    frame.loc[0, "arm_angle"] = np.nan
    frame["events"] = pd.array([None, "single"], dtype="string")

    _install_fake_pybaseball(monkeypatch, frame=frame, pitcher_frame=frame)
    for records in (
        fetch_pitcher_statcast(554430, season=2026),
        fetch_batter_statcast(518692, season=2026),
    ):
        for record in records:
            for key, value in record.items():
                assert not (isinstance(value, float) and value != value), key
                assert value is None or not pd.isna(value), key
        assert records[0]["arm_angle"] is None
        assert records[0]["events"] is None
        assert records[1]["events"] == "single"


def test_statcast_fetchers_skip_absent_columns_rather_than_fabricating(monkeypatch):
    """A column Statcast never returned is omitted, not invented as None.

    arm_angle only exists from 2024. A fabricated None column would be
    indistinguishable from a real missing reading on a pitch that has one.
    """
    row = _sc_row()
    row.pop("arm_angle")
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame([row]))
    record = fetch_pitcher_statcast(554430, season=2026)[0]
    assert "arm_angle" not in record
    assert "stand" in record


def test_statcast_fetchers_reject_pre_statcast_seasons(monkeypatch):
    """Statcast begins in 2015; an earlier season returns [] without a call."""
    calls = _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame([_sc_row()]),
        pitcher_frame=_statcast_frame([_sc_row()]),
    )
    assert fetch_pitcher_statcast(554430, season=2014) == []
    assert fetch_batter_statcast(518692, season=2014) == []
    assert calls == []


def test_statcast_fetchers_return_empty_on_fetch_failure(monkeypatch, capsys):
    """A third-party failure returns [] rather than raising."""
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant down"))
    assert fetch_pitcher_statcast(554430, season=2026) == []
    assert fetch_batter_statcast(518692, season=2026) == []
    assert "Statcast fetch failed" in capsys.readouterr().out


def test_statcast_fetchers_return_empty_on_empty_frame(monkeypatch):
    """An empty frame yields [] and is not cached as a settled answer."""
    _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame([]), pitcher_frame=_statcast_frame([])
    )
    assert fetch_pitcher_statcast(554430, season=2026) == []
    assert fetch_batter_statcast(518692, season=2026) == []


def test_fetch_pitcher_statcast_second_call_hits_the_cache(monkeypatch):
    """A repeat pull in the same run costs no second network call."""
    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame([_sc_row()])
    )
    first = fetch_pitcher_statcast(554430, season=2026)
    second = fetch_pitcher_statcast(554430, season=2026)

    assert len(calls) == 1
    assert len(first) == len(second) == 1


def test_batter_fetchers_share_one_cached_frame(monkeypatch):
    """fetch_batter_statcast reuses the frame fetch_bvp_statcast already cached.

    Both are the same batter-season pull under the same cache role, so a run that
    did Category 1 work first must not pay 3.4s again for Category 2.
    """
    calls = _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame([_sc_row(**{"pitcher": 554430})]),
    )
    fetch_bvp_statcast(518692, 554430, season=2026)
    fetch_batter_statcast(518692, season=2026)
    assert len(calls) == 1


def test_statcast_fetchers_do_not_import_pybaseball_at_module_scope():
    """pybaseball must be imported inside the fetchers, never at module scope.

    A module-scope import drags pandas into every test run and into a daily run
    that never touches Statcast.
    """
    import ast
    import pathlib

    source = pathlib.Path(
        "calculators/sources/category_02_platoon_splits.py"
    ).read_text()
    tree = ast.parse(source)
    for node in tree.body:  # module level only, not nested function bodies
        if isinstance(node, ast.Import):
            assert all("pybaseball" not in n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert "pybaseball" not in (node.module or "")


def test_pa_grouping_survives_the_cache_round_trip():
    """Live-fetched and cache-loaded frames must group into identical PAs.

    `_plate_appearances` keys on the tuple ``(game_pk, at_bat_number)``, and a
    tuple key is type-sensitive: ``(777001, 12)`` and ``(777001.0, 12.0)`` are
    different dict entries. Every other Statcast test builds its frame in memory
    and never round-trips it, so nothing else here would notice if the cache
    changed a column's dtype and split one plate appearance in two.

    It currently holds for a reason worth stating: pandas picks the dtype from
    the data, and the data is the same on both paths — no missing value gives
    int64 both sides, a missing value gives float64 both sides. That is a
    property of pandas' coercion rules, not of anything this code does, which is
    exactly why it deserves a test rather than an assumption.
    """
    import pathlib
    import tempfile

    import numpy as np
    import pandas as pd

    from calculators.sources.category_02_platoon_splits import (
        CATEGORY_02_PITCH_FIELDS,
        _normalize_pitches,
    )
    from calculators.sources.common import (
        _read_statcast_cache,
        _write_statcast_cache,
    )

    # Two pitches of one PA, plus a row with missing identifiers — which is what
    # forces the whole column to float64 on the live path.
    frame = _statcast_frame(
        [
            _sc_row(at_bat=12, game_pk=777001),
            _sc_row(at_bat=12, game_pk=777001, events="single"),
            _sc_row(at_bat=np.nan, game_pk=np.nan),
        ]
    )

    live = _normalize_pitches(frame, CATEGORY_02_PITCH_FIELDS)
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "roundtrip.csv.gz"
        _write_statcast_cache(path, frame)
        reloaded = _read_statcast_cache(path)
        assert reloaded is not None
        cached = _normalize_pitches(reloaded, CATEGORY_02_PITCH_FIELDS)

    def keys(records):
        return sorted(
            (r["game_pk"], r["at_bat_number"])
            for r in records
            if r.get("game_pk") is not None and r.get("at_bat_number") is not None
        )

    assert keys(live) == keys(cached)
    assert len(_plate_appearances(live)) == len(_plate_appearances(cached)) == 1
    # And the outcome survives the trip, not just the grouping.
    assert _plate_appearances(cached)[0]["events"] == "single"
    assert not any(pd.isna(v) for r in cached for v in r.values() if v is not None)
