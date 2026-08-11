"""Generate the league 2x2 platoon baseline that CALC_15 measures against.

Run on demand, never during a daily run or a test:

    uv run python -m scripts.generate_league_platoon_baseline

Writes `calculators/data/league_platoon_baseline.json`, which the calculators
read as a constant. League platoon rates move slowly within a season, so this is
regenerated occasionally rather than fetched per run.

Why a 2x2 and not the vs-L / vs-R pair
--------------------------------------
Aggregated across all hitters, vs-L and vs-R are nearly identical (.2423 and
.2444 measured 2026-08-08) because left- and right-handed batters are pooled and
their opposite platoon advantages cancel. The effect only appears once the
sample is conditioned on batter hand as well, which team-level aggregates cannot
do — teams are not split by batter handedness.

Why the MLB Stats API and not Statcast
--------------------------------------
`pybaseball.statcast()` — the league-wide bulk endpoint — is broken at the
pinned 2.0.0::

    KeyError: "['pitcher.1', 'fielder_2.1'] not in index"
      pybaseball/statcast.py:155, in postprocessing()

Savant no longer returns columns that version's postprocessing requires.
(`statcast_batter` and `statcast_pitcher` are unaffected.) The Stats API path
below is also simply better: 4 requests, no pandas, seconds to run.

Cost: one leaderboard request plus one batched `/people` request per chunk of
ids — 4 total at present roster sizes.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import requests

from mlb_api import MLB_API_BASE

OUTPUT_PATH = pathlib.Path("calculators/data/league_platoon_baseline.json")

# The leaderboard silently truncates at 50 splits without an explicit limit,
# which returns 25 of 30 teams' worth of players and a plausible-looking wrong
# answer. Verified 2026-08-08: default gave .2464/.2467, the full set .2423/.2444.
LEADERBOARD_LIMIT = 3000

# /people takes ids in the query string; chunk so the URL stays a sane length.
PEOPLE_CHUNK = 300

# Switch hitters are excluded: they have no fixed batter hand, so they cannot
# belong to a cell of a batter-hand x pitcher-hand grid. CALC_13 handles them.
BATTER_HANDS = ("L", "R")


def aggregate_platoon_baseline(rows, handedness):
    """Aggregate leaderboard *rows* into the four batter-hand x pitcher-hand cells.

    *rows* is an iterable of ``(player_id, split_code, at_bats, hits,
    plate_appearances)``; *handedness* maps player id to a `batSide` code.

    Pure — no network, no file I/O — so the arithmetic is testable without
    mocking anything. Players whose handedness is unknown or who switch-hit are
    skipped rather than guessed at.
    """
    cells: dict[str, dict[str, int]] = {}
    for player_id, code, at_bats, hits, plate_appearances in rows:
        bats = handedness.get(player_id)
        if bats not in BATTER_HANDS or code not in ("vl", "vr"):
            continue
        key = f"{bats}_vs_{'L' if code == 'vl' else 'R'}"
        cell = cells.setdefault(key, {"at_bats": 0, "hits": 0, "plate_appearances": 0})
        cell["at_bats"] += at_bats or 0
        cell["hits"] += hits or 0
        cell["plate_appearances"] += plate_appearances or 0

    return {
        key: {
            "rate": cell["hits"] / cell["at_bats"] if cell["at_bats"] else None,
            "denominator": cell["at_bats"],
            "plate_appearances": cell["plate_appearances"],
        }
        for key, cell in sorted(cells.items())
    }


def fetch_leaderboard_rows(season: int):
    """Return (player_id, code, ab, h, pa) for every hitter's vl/vr split."""
    resp = requests.get(
        f"{MLB_API_BASE}/stats",
        params={
            "stats": "statSplits",
            "sitCodes": "vl,vr",
            "group": "hitting",
            "season": season,
            "sportId": 1,
            "playerPool": "All",
            "limit": LEADERBOARD_LIMIT,
        },
        timeout=60,
    )
    resp.raise_for_status()

    rows = []
    for group in resp.json().get("stats", []):
        for split in group.get("splits", []):
            player_id = (split.get("player") or {}).get("id")
            if player_id is None:
                continue
            stat = split.get("stat") or {}
            rows.append(
                (
                    player_id,
                    (split.get("split") or {}).get("code"),
                    stat.get("atBats", 0) or 0,
                    stat.get("hits", 0) or 0,
                    stat.get("plateAppearances", 0) or 0,
                )
            )
    return rows


def fetch_handedness_chunked(player_ids):
    """Resolve batSide for every id, chunking to keep each URL a sane length."""
    ids = sorted(set(player_ids))
    handedness = {}
    for start in range(0, len(ids), PEOPLE_CHUNK):
        chunk = ids[start : start + PEOPLE_CHUNK]
        resp = requests.get(
            f"{MLB_API_BASE}/people",
            params={"personIds": ",".join(str(i) for i in chunk)},
            timeout=60,
        )
        resp.raise_for_status()
        for person in resp.json().get("people", []):
            handedness[person["id"]] = (person.get("batSide") or {}).get("code")
    return handedness


def main(season: int | None = None) -> dict:
    season = season or date.today().year
    rows = fetch_leaderboard_rows(season)
    handedness = fetch_handedness_chunked(player_id for player_id, *_ in rows)
    document = {
        "season": season,
        "generated": date.today().isoformat(),
        "source": "MLB Stats API statSplits leaderboard + batched /people handedness",
        "note": (
            "rate is batting average (hits / at_bats) for the batter-hand x "
            "pitcher-hand cell. Switch hitters are excluded; see CALC_13."
        ),
        "splits": aggregate_platoon_baseline(rows, handedness),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n")
    return document


if __name__ == "__main__":
    doc = main()
    print(f"wrote {OUTPUT_PATH} (season {doc['season']})")
    for key, cell in doc["splits"].items():
        print(f"  {key}: BA={cell['rate']:.4f} over {cell['denominator']} AB")
