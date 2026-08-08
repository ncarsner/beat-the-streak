"""Tests for calculators/sources/category_02_platoon_splits.py.

All tests mock requests.get — no live API call is ever made.
"""

import requests

from calculators.sources.category_02_platoon_splits import fetch_handedness
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
