"""Helpers shared across all source modules.

Nothing here touches the network — only utilities needed by multiple
source modules, such as pandas-sentinel cleaning and the Statcast
response cache.
"""

from __future__ import annotations

import pathlib
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def _clean(value, isna):
    """Convert any pandas missing-value sentinel to None, leaving the rest alone.

    Takes pandas' own `isna` rather than testing for NaN by hand: pandas has
    more than one missing sentinel, and they are not interchangeable. `np.nan`
    is a float that fails an equality check against itself, but `pd.NA` — what a
    nullable string column yields, and `events` is unset on every pitch that
    does not end a plate appearance — is neither a float nor comparable, so a
    hand-rolled NaN check passes it straight through to calculators promised
    they would only ever see None.
    """
    if value is None:
        return None
    try:
        missing = bool(isna(value))
    except (TypeError, ValueError):
        return value
    return None if missing else value


# ---------------------------------------------------------------------------
# Statcast response cache
# ---------------------------------------------------------------------------
#
# Cache files live at .cache/statcast/{role}_{player_id}_{season}[_{date}].csv.gz
#
# Key design choices:
#   - Past seasons are immutable: their frames never change, so the key has no
#     date component and the file is served forever.
#   - The current season is date-keyed: a new file is written each day, and
#     yesterday's file is simply not found (cache miss).  Stale files accumulate
#     but are gitignored and harmless.
#   - The raw per-player frame is cached BEFORE pitcher filtering. This lets
#     multiple batter-pitcher matchups within the same run reuse one network
#     call (the 3.4 s saving is per batter, not per matchup pair).
#   - _clean(row[field], pd.isna) runs AFTER the frame is loaded from cache,
#     so CSV's None→NaN round-trip is normalized on the way out just as it is
#     on the live-fetch path. No special handling is required in the cache layer.

CACHE_DIR = pathlib.Path(".cache/statcast")


def _cache_path(role: str, player_id: int, season: int) -> pathlib.Path:
    """Return the expected cache file path for this role/player/season.

    Current-season entries embed today's date so a stale in-season file
    is never found (it has a different name), making cache invalidation
    entirely path-based — no timestamp comparison needed.
    """
    current_season = date.today().year
    if season < current_season:
        filename = f"{role}_{player_id}_{season}.csv.gz"
    else:
        filename = f"{role}_{player_id}_{season}_{date.today().isoformat()}.csv.gz"
    return CACHE_DIR / filename


def _read_statcast_cache(path: pathlib.Path) -> pd.DataFrame | None:
    """Load a cached Statcast frame, or return None on miss or any read error.

    A corrupt or partially-written file falls back to a live fetch rather than
    raising, matching the fetcher's own error contract.
    """
    if not path.exists():
        return None
    try:
        import pandas as pd

        return pd.read_csv(path, compression="gzip")
    except Exception as exc:  # noqa: BLE001
        print(f"Statcast cache read error ({path.name}): {exc}; re-fetching")
        return None


def _write_statcast_cache(path: pathlib.Path, frame: pd.DataFrame) -> None:
    """Persist *frame* to the cache at *path* as a gzip-compressed CSV.

    Silently swallows write errors: a failed write means the next run
    re-fetches, which is safe.  pybaseball is not imported here — the caller
    holds the DataFrame and passes it in.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, compression="gzip")
    except Exception as exc:  # noqa: BLE001
        print(f"Statcast cache write error ({path.name}): {exc}")
