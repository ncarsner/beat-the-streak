"""Network fetchers that feed the Category 2 platoon-splits calculators.

`fetch_handedness` issues one GET /people?personIds=... request for a batch
of player ids and returns batSide.code / pitchHand.code for each, so a
single network call covers an entire lineup rather than one call per player.

The response envelope is {"people": [...]}, the standard MLB Stats API shape
for the /people endpoint (same envelope as /people/{id}). This was verified
against the live API on 2026-08-08.
"""

from __future__ import annotations

from collections.abc import Collection

import requests

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
