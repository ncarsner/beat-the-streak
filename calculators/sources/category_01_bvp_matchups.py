"""Network fetchers that feed the Category 1 BvP calculators.

`fetch_bvp_stats` issues one MLB Stats API request per batter and returns a
normalized BvP payload (career + by-season splits).

`fetch_bvp_statcast` pulls one season of Statcast pitch records and filters to
the batter-pitcher matchup locally.

Both return a safe empty payload on failure rather than raising, so a category
calculator never sees a network error.
"""

from datetime import date, datetime

import requests

from calculators.category_01_bvp_matchups import parse_bvp_stats
from calculators.sources.common import _clean
from mlb_api import MLB_API_BASE


def empty_bvp() -> dict:
    """What a failed or absent BvP lookup normalizes to.

    A function, not a module constant: the payload nests a mutable dict, and a
    shared instance would let one caller's edit leak into every later lookup.
    """
    return {"career": None, "by_season": {}}


# Statcast pitch fields the Category 1 calculators read. Anything else in the
# response is dropped at the boundary so the pure calculators never see a
# DataFrame or a NaN.
STATCAST_FIELDS = (
    "description",
    "events",
    "strikes",
    "launch_speed",
    "estimated_ba_using_speedangle",
    "estimated_woba_using_speedangle",
)

# Statcast's first full season; nothing earlier can be fetched.
STATCAST_FIRST_SEASON = 2015


def fetch_bvp_statcast(
    batter_id: int,
    pitcher_id: int,
    season: int | None = None,
) -> list[dict]:
    """Return one normalized record per pitch *batter_id* saw from *pitcher_id*.

    Feeds CALC_05-08. Scoped to a single *season* (the current year by default)
    because `statcast_batter` pulls a full season of pitches per call — every
    pitch the batter saw from anyone — and the matchup is filtered out of that
    locally. Statcast starts in 2015, so an earlier season returns [].

    Returns [] rather than raising if the fetch fails. pybaseball is imported
    inside the function: it pulls in pandas, and nothing in a run needs it.
    """
    season = season or datetime.today().year
    if season < STATCAST_FIRST_SEASON:
        return []
    start = f"{season}-01-01"
    end = min(date.today(), date(season, 12, 31)).isoformat()

    try:
        import pandas as pd
        from pybaseball import statcast_batter

        frame = statcast_batter(start, end, batter_id)
    except Exception as exc:  # noqa: BLE001 - third-party call, failure modes undocumented
        print(f"Statcast fetch failed for batter {batter_id} ({exc})")
        return []

    if frame is None or frame.empty or "pitcher" not in frame.columns:
        return []

    matchup = frame[frame["pitcher"] == pitcher_id]
    present = [f for f in STATCAST_FIELDS if f in matchup.columns]
    return [
        {field: _clean(row[field], pd.isna) for field in present}
        for _, row in matchup[present].iterrows()
    ]


def fetch_bvp_stats(batter_id: int, pitcher_id: int) -> dict:
    """Fetch head-to-head splits for *batter_id* against *pitcher_id*.

    One request feeds every Category 1 calculator: `stats=vsPlayer` returns a
    split per season the pair has faced each other plus a career total, so
    CALC_01-04 need no follow-up calls. Returns an empty normalized payload on
    request failure rather than raising.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/{batter_id}/stats",
            params={
                "stats": "vsPlayer",
                "group": "hitting",
                "opposingPlayerId": pitcher_id,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"BvP fetch error for batter {batter_id} vs pitcher {pitcher_id} ({exc})")
        return empty_bvp()
    return parse_bvp_stats(resp.json())
