"""Shared test helpers used across multiple test modules.

Plain functions (not fixtures) so test bodies can call them directly without
changing function signatures. Importable as ``from tests.conftest import ...``.

Also installs the autouse network guard below, which enforces the suite's
central invariant: no test ever reaches the live API.
"""

import socket

import pytest
import requests


# Loopback stays reachable so a future test can talk to a local fixture server.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class LiveNetworkAttempted(BaseException):
    """Raised when a test tries to open a real connection.

    Derives from `BaseException`, not `Exception`, and that is the whole point.
    `fetch_bvp_statcast` wraps its pybaseball call in a deliberately broad
    `except Exception` because the library's failure modes are undocumented — so
    an `Exception` here gets swallowed, the fetcher returns `[]`, and the leaking
    test passes green while the guard silently does nothing. Verified: the first
    version of this guard raised `RuntimeError` and the leak it was written to
    catch still passed. pytest reports a `BaseException` as a failure, so this
    propagates through every `except Exception` in the codebase.
    """


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Fail any test that opens a real network connection.

    The suite is offline by design: every fetcher is monkeypatched and every
    payload is a fixture. That invariant was stated in the docs long before
    anything enforced it, and it silently broke twice — both times because a
    test patched one of the two fetchers `attach_category_01` calls and left the
    other live. Neither failed. Neither was even red; the only symptom was a test
    that took 0.43s in a suite where nothing else exceeded 0.05s.

    Blocking at the socket layer rather than at `requests` is deliberate: the
    calls that escaped came from pybaseball, which does its own HTTP and never
    touches a `requests` symbol a test thought to patch. A connection is the one
    thing every escape route has in common.
    """

    def _blocked(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in _ALLOWED_HOSTS:
            return _real_connect(self, address, *args, **kwargs)
        raise LiveNetworkAttempted(
            f"Test attempted a live network connection to {host!r}. The suite "
            "must never reach the live API — patch the fetcher this code path "
            "calls. Note that attach_category_01 calls two of them: "
            "fetch_bvp_stats and fetch_bvp_statcast."
        )

    def _blocked_ex(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in _ALLOWED_HOSTS:
            return _real_connect_ex(self, address, *args, **kwargs)
        raise LiveNetworkAttempted(
            f"Test attempted a live network connection to {host!r}."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_ex)


@pytest.fixture(autouse=True)
def _redirect_statcast_cache(tmp_path, monkeypatch):
    """Redirect the Statcast cache to a per-test temp directory.

    Without this, a real .cache/statcast/ file on disk can satisfy a cache
    lookup inside fetch_bvp_statcast, causing the test to skip the stubbed
    pybaseball call and silently pass or fail for the wrong reason.
    """
    import calculators.sources.common as _sources_common

    monkeypatch.setattr(_sources_common, "CACHE_DIR", tmp_path / "statcast")


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


# ---- BvP payload builders (shared by category_01 calculator and source tests) ----


def _split(season=None, pa=0, ab=0, h=0, so=0, bb=0):
    stat = {
        "plateAppearances": pa,
        "atBats": ab,
        "hits": h,
        "strikeOuts": so,
        "baseOnBalls": bb,
    }
    return {"season": season, "stat": stat}


def _vsplayer_payload(season_splits, total=None):
    """Build a raw stats=vsPlayer response with optional vsPlayerTotal group."""
    stats = [{"type": {"displayName": "vsPlayer"}, "splits": season_splits}]
    if total is not None:
        stats.append({"type": {"displayName": "vsPlayerTotal"}, "splits": [total]})
    return {"stats": stats}


# ---- Statcast pitch-record builder (shared by category_01 and source tests) ----


def _pitch(description="ball", events=None, strikes=0, ev=None, xba=None, xwoba=None):
    return {
        "description": description,
        "events": events,
        "strikes": strikes,
        "launch_speed": ev,
        "estimated_ba_using_speedangle": xba,
        "estimated_woba_using_speedangle": xwoba,
    }


# ---- pybaseball stub (shared by source tests) ----


def _install_fake_pybaseball(monkeypatch, frame=None, exc=None):
    """Stub the pybaseball import inside fetch_bvp_statcast."""
    import sys
    import types

    calls = []

    def statcast_batter(start_dt, end_dt, player_id):
        calls.append((start_dt, end_dt, player_id))
        if exc is not None:
            raise exc
        return frame

    module = types.ModuleType("pybaseball")
    module.statcast_batter = statcast_batter
    monkeypatch.setitem(sys.modules, "pybaseball", module)
    return calls


def _statcast_frame(rows):
    import pandas as pd

    return pd.DataFrame(rows)
