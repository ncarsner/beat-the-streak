"""Category 6 — Lineup & Game Context Factors (`CALC_41`).

Pure functions; no network, no imports from the sources package. See
`ROADMAP.md` for the source definition of each `CALC_NN`.

Only `CALC_41` is implemented. The rest of Category 6 is out of scope for this
increment, and `CALC_44`/`CALC_45`/`CALC_51` were reinterpreted during triage as
`PA_proj` adjustments rather than `p_hit` terms — they change how many plate
appearances a hitter gets and against whom, which is what this module owns.

**Nothing here is wired into a run.** `main.binomial_probability` still computes
its exponent as `pa / 5`, and the ranked table is unchanged. `CALC_41` is built
and tested for the composite model to consume later.
"""

from __future__ import annotations

from typing import Any

from calculators.common import EXPONENT, Rate

# Projected plate appearances by batting-order spot, per ROADMAP CALC_41, which
# fixes the endpoints at 4.6 for the leadoff spot and 3.7 for ninth. The interior
# spots are interpolated linearly across that range: a full lineup turn is nine
# spots, and each spot later in the order is one turn further from the next PA.
#
# This is a league-shaped prior, not a player projection. It knows something a
# player's own PA history cannot — that today's leadoff man is batting eighth —
# and does not know what that history does, namely whether he is a regular. The
# composite model is where the two get reconciled.
LINEUP_SPOT_PA = {
    1: 4.60,
    2: 4.49,
    3: 4.38,
    4: 4.26,
    5: 4.15,
    6: 4.04,
    7: 3.93,
    8: 3.81,
    9: 3.70,
}


def calc_41_lineup_spot_pa_expectation(lineup_spot: int | None) -> EXPONENT | None:
    """CALC_41 — projected plate appearances from the batting-order spot.

    Role is `EXPONENT`: this feeds `PA_proj`, the exponent in
    ``P(>=1 hit) = 1 - (1 - p_hit)^PA_proj``. It is emphatically not a
    probability and must never be blended into `p_hit`.

    Returns None for a missing or out-of-range spot rather than extrapolating.
    The mapping is defined on the nine spots a lineup has; anything else is a
    shape this calculator does not understand.

    The `Rate` denominator is the spot itself rather than a sample size — there
    is no sample here, this is a lookup. Callers reading a denominator as
    evidence weight should treat an `EXPONENT` as carrying none.
    """
    projected = LINEUP_SPOT_PA.get(lineup_spot)
    if projected is None:
        return None
    return EXPONENT(Rate(projected, lineup_spot))


def compute_category_06(lineup_spot: int | None = None) -> dict[str, Any]:
    """Run every implemented Category 6 calculator.

    Absent inputs resolve to ``None`` rather than omitting the key, matching the
    contract of `compute_category_01` and `compute_category_02`.
    """
    return {"CALC_41": calc_41_lineup_spot_pa_expectation(lineup_spot)}
