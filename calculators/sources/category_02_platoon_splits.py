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
from typing import Literal

import requests

from calculators.common import COUNTING_STATS
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
