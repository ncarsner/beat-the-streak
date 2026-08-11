"""Source layer for the calculator package.

The only part of the calculators/ tree that touches the network. Pure
calculator modules never import from here, which is what keeps their
arithmetic testable without mocking a request.

One sub-module per ROADMAP category that requires a fetcher:

- ``category_01_bvp_matchups`` — BvP (vsPlayer) and Statcast pitch fetchers.

``attach_category_01`` lives here rather than in the category sub-module so
that test patches on the ``sources`` namespace are visible to the calls it
makes (Python resolves names against the module where a function is *defined*,
not where it is *called from*).
"""

from datetime import datetime

from calculators.category_01_bvp_matchups import compute_category_01
from calculators.sources.category_01_bvp_matchups import (
    STATCAST_FIELDS,
    STATCAST_FIRST_SEASON,
    empty_bvp,
    fetch_bvp_statcast,
    fetch_bvp_stats,
)


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

    Costs two fetches per batter when a starter is known: one MLB Stats API
    request for CALC_01-04, and one Statcast season pull for CALC_05-08.
    """
    season = season or datetime.today().year
    if pitcher_id is None:
        results = compute_category_01(empty_bvp(), season)
    else:
        results = compute_category_01(
            fetch_bvp_stats(batter_id, pitcher_id),
            season,
            fetch_bvp_statcast(batter_id, pitcher_id, season),
        )
    player_data.update(results)
    return player_data


__all__ = [
    "STATCAST_FIELDS",
    "STATCAST_FIRST_SEASON",
    "attach_category_01",
    "empty_bvp",
    "fetch_bvp_statcast",
    "fetch_bvp_stats",
]
