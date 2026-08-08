"""Shared test helpers used across multiple test modules.

Plain functions (not fixtures) so test bodies can call them directly without
changing function signatures. Importable as ``from tests.conftest import ...``.
"""

import requests


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
