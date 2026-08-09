"""Network fetchers that feed the Category 2 platoon-splits calculators.

`fetch_handedness` issues one GET /people?personIds=... request for a batch
of player ids and returns batSide.code / pitchHand.code for each, so a
single network call covers an entire lineup rather than one call per player.

`fetch_stat_splits` issues one GET /people/{id}/stats?stats=statSplits&sitCodes=vl,vr
for a player and season, returning vl/vr splits keyed by code as COUNTING_STATS
lines. For a hitter, 'vl' means vs Left-Handed Pitchers; for a pitcher, 'vl'
means vs Left-Handed Batters. Because date ranges and sitCodes cannot be
combined on the MLB Stats API (verified 2026-08-08: byDateRange honored the
window but dropped the split code=None, while statSplits honored the split
but returned full-season data regardless of byDateRange), this fetcher is
season-scoped only. A DAYS(14) window requires Statcast (see CALC_10/CALC_12).

For the pitching group, `battersFaced` is mapped onto `plateAppearances` at the
source boundary so both groups yield the same COUNTING_STATS line shape. Without
this normalization CALC_11 would silently return None for every pitcher (verified
on Wheeler: battersFaced=261, atBats=236, plateAppearances=None, 2026-08-08).

The /people response envelope is {"people": [...]}, the standard MLB Stats API
shape for the /people endpoint (same envelope as /people/{id}). This was
verified against the live API on 2026-08-08.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime
from typing import Literal

import requests

from calculators.common import COUNTING_STATS
from calculators.sources.category_01_bvp_matchups import STATCAST_FIRST_SEASON
from calculators.sources.common import (
    _cache_path,
    _clean,
    _read_statcast_cache,
    _write_statcast_cache,
)
from mlb_api import MLB_API_BASE


def fetch_handedness(
    player_ids: Collection[int],
) -> dict[int, dict[str, str | None]]:
    """Return bats/throws codes for every id in *player_ids* in one request.

    Issues one GET /people?personIds=<comma-separated> to the MLB Stats API.
    A switch hitter is identified by batSide.code == 'S'.

    An id absent from the response is absent from the returned mapping — the
    caller uses ``.get(id)`` which returns None rather than raising a KeyError.

    Returns an empty mapping on requests.RequestException rather than raising,
    matching the error contract of fetch_bvp_stats.

    The response is normalized to {player_id: {'bats': code, 'throws': code}};
    no raw response shape escapes this function.

    Note: no chunking is applied here. For large id collections the caller is
    responsible for batching to stay within URL length limits.
    """
    if not player_ids:
        return {}

    ids_param = ",".join(str(pid) for pid in player_ids)
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people",
            params={"personIds": ids_param},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Handedness fetch error for ids {ids_param!r} ({exc})")
        return {}

    result: dict[int, dict[str, str | None]] = {}
    for person in resp.json().get("people", []):
        pid = person.get("id")
        if pid is None:
            continue
        result[pid] = {
            "bats": (person.get("batSide") or {}).get("code"),
            "throws": (person.get("pitchHand") or {}).get("code"),
        }

    return result


# ---------------------------------------------------------------------------
# statSplits fetcher — vl/vr season splits for hitting or pitching
# ---------------------------------------------------------------------------


def empty_stat_splits() -> dict[str, dict[str, int]]:
    """What a failed or absent statSplits lookup normalizes to."""
    return {}


def _parse_stat_splits(
    payload: dict,
    group: Literal["hitting", "pitching"],
) -> dict[str, dict[str, int]]:
    """Normalize a raw stats=statSplits API response into {code: COUNTING_STATS line}.

    *group* is ``'hitting'`` or ``'pitching'``. When *group* is ``'pitching'``,
    the split's ``battersFaced`` value is used as ``plateAppearances`` because
    the pitching stat group carries no ``plateAppearances`` key (verified on
    Wheeler: battersFaced=261, atBats=236, plateAppearances=None, 2026-08-08).
    Without this substitution ``rate_or_none`` would receive a zero denominator
    and return None for every pitcher.

    Malformed or empty input normalizes to ``{}`` rather than raising, matching
    the defensive style of ``parse_bvp_stats``.
    """
    result: dict[str, dict[str, int]] = {}
    for stat_group in payload.get("stats") or []:
        for split in stat_group.get("splits") or []:
            code = (split.get("split") or {}).get("code")
            if code not in ("vl", "vr"):
                continue
            stat = split.get("stat") or {}
            line: dict[str, int] = {}
            for field in COUNTING_STATS:
                if field == "plateAppearances" and group == "pitching":
                    # Pitching splits carry battersFaced, not plateAppearances.
                    val = stat.get("battersFaced", stat.get("plateAppearances", 0))
                else:
                    val = stat.get(field, 0)
                line[field] = val or 0
            result[code] = line
    return result


def fetch_stat_splits(
    player_id: int,
    group: Literal["hitting", "pitching"],
    season: int,
) -> dict[str, dict[str, int]]:
    """Return vl/vr statSplits for *player_id* in *season* for the given stat *group*.

    Issues one GET /people/{player_id}/stats?stats=statSplits&sitCodes=vl,vr.
    The returned mapping is keyed by split code:

    - ``'vl'``: for a hitter, vs Left-Handed Pitchers; for a pitcher, vs Left-Handed Batters
    - ``'vr'``: for a hitter, vs Right-Handed Pitchers; for a pitcher, vs Right-Handed Batters

    Note: ``sitCodes`` and date ranges cannot be combined on the MLB Stats API —
    this fetcher is season-scoped only. Recency windows (e.g. DAYS(14)) require
    Statcast pitch-level data; see CALC_10 / CALC_12.

    Returns an empty mapping on request failure rather than raising, matching the
    error contract of ``fetch_bvp_stats``.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/{player_id}/stats",
            params={
                "stats": "statSplits",
                "group": group,
                "sitCodes": "vl,vr",
                "season": season,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"statSplits fetch error for player {player_id} ({exc})")
        return empty_stat_splits()
    return _parse_stat_splits(resp.json(), group)


# ---------------------------------------------------------------------------
# Statcast pitch-level fetchers
# ---------------------------------------------------------------------------
#
# Two fetchers, not one, because the two sides of a matchup are different rows.
# `statcast_pitcher` returns the pitches a pitcher threw; `statcast_batter`
# returns the pitches a batter saw. A calculator needs whichever side owns the
# population it averages over:
#
#   CALC_10  hitter's rate vs. pitcher hand      -> batter side (filter p_throws)
#   CALC_12  pitcher's rate allowed vs. bat side -> pitcher side (filter stand)
#   CALC_14  hitter's rate vs. arm-angle bucket  -> batter side (filter arm_angle)
#
# CALC_14 is the reason the batter fetcher exists. The PRD assigns it to the
# pitcher fetcher, but it is defined as *the hitter's* rate against pitchers of a
# given slot — averaging over the hitter's pitches, not one pitcher's. Reading it
# off pitcher-side rows would answer a different question (how that one pitcher
# fared), and would have no sample at all for a hitter facing that slot for the
# first time.
#
# The batter fetcher shares the "batter" cache role with fetch_bvp_statcast, so a
# run that already pulled a batter's season for Category 1 pays no second call.

# Pitch fields the Category 2 calculators read. Wider than Category 1's set:
# `game_date` scopes a DAYS(n) window, `game_pk`/`at_bat_number` together
# identify a plate appearance so a rate can be per-PA rather than per-pitch, and
# `stand`/`p_throws`/`arm_angle` are the handedness and slot filters.
CATEGORY_02_PITCH_FIELDS = (
    "game_date",
    "game_pk",
    "at_bat_number",
    "events",
    "description",
    "stand",
    "p_throws",
    "arm_angle",
    "estimated_ba_using_speedangle",
    "estimated_woba_using_speedangle",
)


def _normalize_pitches(frame, fields: tuple[str, ...] = CATEGORY_02_PITCH_FIELDS):
    """Return one plain dict per row of *frame*, restricted to *fields*.

    Columns absent from the frame are skipped rather than filled: Statcast has
    added columns over the years (`arm_angle` only exists from 2024), and a
    fabricated None column would be indistinguishable from a real missing
    reading. Every pandas missing sentinel is converted to None here, so no
    calculator ever sees a NaN or a pd.NA.
    """
    import pandas as pd

    present = [f for f in fields if f in frame.columns]
    return [
        {field: _clean(row[field], pd.isna) for field in present}
        for _, row in frame[present].iterrows()
    ]


def _fetch_season_statcast(role: str, player_id: int, season: int | None):
    """Return a cached-or-fetched season frame for *player_id*, or None.

    Shared by both fetchers; *role* is ``'batter'`` or ``'pitcher'`` and selects
    both the pybaseball entry point and the cache namespace. pybaseball is
    imported inside this function, never at module scope — it drags in pandas,
    and neither a daily run nor most of the test suite needs it.
    """
    season = season or datetime.today().year
    if season < STATCAST_FIRST_SEASON:
        return None
    start = f"{season}-01-01"
    end = min(date.today(), date(season, 12, 31)).isoformat()

    cache_path = _cache_path(role, player_id, season)
    frame = _read_statcast_cache(cache_path)
    if frame is not None:
        return frame

    if role == "batter":
        from pybaseball import statcast_batter as pull
    else:
        from pybaseball import statcast_pitcher as pull

    frame = pull(start, end, player_id)
    # Only cache a populated frame. An empty result means no Statcast coverage
    # for this player/season yet; re-fetching next run is correct, so absence is
    # left unsettled rather than frozen into the cache.
    if frame is not None and not frame.empty:
        _write_statcast_cache(cache_path, frame)
    return frame


def fetch_pitcher_statcast(pitcher_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *pitcher_id* threw in *season*.

    Feeds CALC_12, which filters these by `stand` and `game_date`. Statcast
    begins in 2015, so an earlier season returns []. Returns [] rather than
    raising on failure, matching every other fetcher's error contract.
    """
    try:
        frame = _fetch_season_statcast("pitcher", pitcher_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast fetch failed for pitcher {pitcher_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame)


def fetch_batter_statcast(batter_id: int, season: int | None = None) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw in *season*.

    Feeds CALC_10 (filtered by `p_throws`) and CALC_14 (bucketed by `arm_angle`).
    Unlike `fetch_bvp_statcast` this is not filtered to one opposing pitcher —
    these calculators average over the batter's whole season against a *class* of
    pitcher, not one matchup. Shares the "batter" cache role with
    `fetch_bvp_statcast`, so a run that already pulled this batter's season pays
    no second call.
    """
    try:
        frame = _fetch_season_statcast("batter", batter_id, season)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast fetch failed for batter {batter_id} ({exc})")
        return []
    if frame is None or frame.empty:
        return []
    return _normalize_pitches(frame)
