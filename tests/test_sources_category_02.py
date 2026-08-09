"""Tests for calculators/sources/category_02_platoon_splits.py.

All tests mock requests.get — no live API call is ever made.
"""

import requests

from calculators.sources.category_02_platoon_splits import (
    _parse_stat_splits,
    empty_stat_splits,
    fetch_handedness,
    fetch_stat_splits,
)
from tests.conftest import FakeResponse


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
