"""MLB Stats API fetches that feed the calculator modules.

The only module in this package that touches the network. Pure calculator
modules never import it, which is what keeps their arithmetic testable without
mocking a request. Fetchers are named `fetch_<subject>` and return a normalized
payload — never a raw response — so a category's parser stays the single place
that knows the API's response shape.

Nothing here is called during a run yet; these are the seams the composite model
(`CALC_75`) will call once there is something to weight.
"""

from datetime import datetime

import requests

from calculators.category_01_bvp_matchups import compute_category_01, parse_bvp_stats
from calculators.common import Rate
from mlb_api import MLB_API_BASE


def empty_bvp() -> dict:
    """What a failed or absent BvP lookup normalizes to.

    A function, not a module constant: the payload nests a mutable dict, and a
    shared instance would let one caller's edit leak into every later lookup.
    """
    return {"career": None, "by_season": {}}


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


def attach_category_01(
    player_data: dict,
    batter_id: int,
    pitcher_id: int | None,
    season: int | None = None,
) -> dict:
    """Add CALC_01-04 to *player_data* in place and return it.

    With no announced opposing starter — or a lineup entry cached before that
    field existed — every calculator resolves to None rather than being omitted,
    so consumers can rely on the keys being present without a request having
    been made.
    """
    season = season or datetime.today().year
    bvp = (
        fetch_bvp_stats(batter_id, pitcher_id)
        if pitcher_id is not None
        else empty_bvp()
    )
    results: dict[str, Rate | None] = compute_category_01(bvp, season)
    player_data.update(results)
    return player_data
