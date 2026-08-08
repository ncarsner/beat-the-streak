"""Tests for the Statcast response cache in calculators.sources.common.

Cache behaviour is exercised through fetch_bvp_statcast, which is the only
production caller.  The ``_redirect_statcast_cache`` fixture in conftest.py
redirects CACHE_DIR to a per-test temp directory, so these tests start with
an empty cache and real .cache/ files on disk never interfere.
"""

import datetime
import pathlib

import pandas as pd

import calculators.sources.common as sources_common
from calculators.sources.category_01_bvp_matchups import fetch_bvp_statcast
from tests.conftest import _install_fake_pybaseball, _statcast_frame

# Shared player ids used throughout; no real Statcast data for these in the
# test cache directory.
_BATTER = 11111
_PITCHER = 22222
_SEASON = datetime.date.today().year
_PAST_SEASON = _SEASON - 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fake_cache(path: pathlib.Path, rows: list[dict]) -> None:
    """Write a minimal gzip CSV that fetch_bvp_statcast can load from cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, compression="gzip")


def _minimal_row(pitcher_id: int) -> dict:
    """One pitch record for *pitcher_id* — enough for the filter to find."""
    return {
        "pitcher": float(pitcher_id),  # CSV round-trips ints as floats
        "description": "foul",
        "events": None,
        "strikes": 1,
        "launch_speed": None,
        "estimated_ba_using_speedangle": None,
        "estimated_woba_using_speedangle": None,
    }


# ---------------------------------------------------------------------------
# cache_path shape
# ---------------------------------------------------------------------------


def test_cache_path_past_season_has_no_date():
    """Past-season paths must be date-free so they never self-invalidate."""
    path = sources_common._cache_path("batter", _BATTER, _PAST_SEASON)
    assert path.name == f"batter_{_BATTER}_{_PAST_SEASON}.csv.gz"
    assert datetime.date.today().isoformat() not in path.name


def test_cache_path_current_season_embeds_today():
    """Current-season paths embed today so yesterday's file is not found."""
    today = datetime.date.today().isoformat()
    path = sources_common._cache_path("batter", _BATTER, _SEASON)
    assert today in path.name


# ---------------------------------------------------------------------------
# cache hit — current season
# ---------------------------------------------------------------------------


def test_cache_hit_returns_filtered_records(monkeypatch):
    """A cache-populated frame is filtered and returned without calling pybaseball."""
    cache_path = sources_common._cache_path("batter", _BATTER, _SEASON)
    _write_fake_cache(
        cache_path,
        [
            _minimal_row(_PITCHER),
            _minimal_row(99999),
        ],  # second row is a different pitcher
    )
    # Stub pybaseball to raise: if pybaseball is touched the test fails.
    _install_fake_pybaseball(
        monkeypatch, exc=RuntimeError("pybaseball must not be called on a cache hit")
    )

    result = fetch_bvp_statcast(_BATTER, _PITCHER, season=_SEASON)

    assert len(result) == 1
    assert result[0]["description"] == "foul"


# ---------------------------------------------------------------------------
# stale-date miss — current season
# ---------------------------------------------------------------------------


def test_stale_cache_entry_triggers_refetch(monkeypatch):
    """A current-season file dated before today is not found → pybaseball is called."""
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    stale_path = (
        sources_common.CACHE_DIR / f"batter_{_BATTER}_{_SEASON}_{yesterday}.csv.gz"
    )
    _write_fake_cache(stale_path, [_minimal_row(_PITCHER)])

    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    fetch_bvp_statcast(_BATTER, _PITCHER, season=_SEASON)

    assert len(calls) == 1, "pybaseball should have been called for the stale miss"


# ---------------------------------------------------------------------------
# past-season immutability
# ---------------------------------------------------------------------------


def test_past_season_cache_served_regardless_of_date(monkeypatch):
    """A past-season cache entry is served without any date check."""
    cache_path = sources_common._cache_path("batter", _BATTER, _PAST_SEASON)
    _write_fake_cache(cache_path, [_minimal_row(_PITCHER)])

    # Stub pybaseball to raise; cache hit must not call it.
    _install_fake_pybaseball(
        monkeypatch,
        exc=RuntimeError("pybaseball must not be called for a past-season cache hit"),
    )

    result = fetch_bvp_statcast(_BATTER, _PITCHER, season=_PAST_SEASON)

    assert len(result) == 1


# ---------------------------------------------------------------------------
# corrupt-file fallback
# ---------------------------------------------------------------------------


def test_corrupt_cache_falls_back_to_live_fetch(monkeypatch, capsys):
    """A corrupt or truncated cache file falls back to pybaseball rather than raising."""
    cache_path = sources_common._cache_path("batter", _BATTER, _SEASON)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"not a valid gzip file at all")

    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame([]))
    result = fetch_bvp_statcast(_BATTER, _PITCHER, season=_SEASON)

    # The corrupt-read warning should appear.
    assert "cache read error" in capsys.readouterr().out.lower()
    # pybaseball was called as the fallback.
    assert len(calls) == 1
    assert result == []


# ---------------------------------------------------------------------------
# cache transparency — no pure calculator knows the cache exists
# ---------------------------------------------------------------------------


def test_pure_calculator_module_does_not_import_cache():
    """The pure calculator module must not import any cache symbol."""
    import ast
    import pathlib

    src = pathlib.Path("calculators/category_01_bvp_matchups.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if node.module else [])
            )
            for name in names:
                assert "sources" not in (name or ""), (
                    f"Pure calculator module imports from sources: {name!r}"
                )
