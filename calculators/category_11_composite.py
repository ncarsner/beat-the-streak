"""Category 11 -- Composite Model Aggregation and Probability Outputs (`CALC_72`-`CALC_76`).

The end of the pipeline. Every other category produces evidence about one hitter
in one matchup; this one combines that evidence into a single per-plate-appearance
hit probability and then into the number the tool actually ranks on.

    CALC_72  season base rate      \\
    CALC_73  14-day weighted rate   >-- CALC_75 posterior --> CALC_76 P(>=1 hit)
    CALC_74  matchup-conditioned   /

Scope: traditional starting pitchers only
------------------------------------------
Per the user, 2026-08-09: this tool ranks scheduled hitters against traditional
starting pitchers, and bullpens are disregarded. So **`CALC_74` takes no Category
7 input**. `CALC_47`, `CALC_48`, `CALC_49` and `CALC_50` measure a bullpen and are
deliberately out of scope for the composite; `CALC_51` was promoted out of the
category entirely and now gates the run through `main.is_opener`. Category 7 is
benched, not deleted, and the modules stay so a later decision can use them.

What this module does not decide
---------------------------------
Issue #34 owns the shrinkage rule and #39 owns how non-rate values map into
`p_hit`. Neither is resolved here, and neither had to be, because of one
distinction: **a number that comes from the data can be computed, and a number
that comes from nowhere has to be a parameter.**

Every `Rate` in this model carries its own denominator, so sample-size weighting
across `CALC_72`, `CALC_73` and `CALC_74` is derived, not invented. The one
quantity that is not in the data is how much the prior should be trusted relative
to the evidence, so `prior_strength` is a parameter, and its default is the
season sample's own size, which makes `CALC_75` a plain precision-weighted mean
until #34 says otherwise. Nothing here invents a league constant, which is the
line `CALC_34` and `CALC_46` drew.

Everything on the `p_hit` scale, and nothing else
--------------------------------------------------
Every input this module accepts is a per-plate-appearance hit rate. It takes no
`MULTIPLIER`, no `DELTA` and no `EXPONENT` except through `PA_proj`, which is a
separate lane. That is not a simplification, it is the reason the module can ship
while #39 is open: the mapping #39 owes is for values that are *not* on this
scale, and none of those are used here.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Sequence

from calculators.category_08_batter_form import plate_appearances
from calculators.common import (
    DAYS,
    HIT_EVENTS,
    EXPONENT,
    PROBABILITY,
    Rate,
    apply_window,
    rate_or_none,
)

# Half-life in days for `CALC_73`'s recency weighting. A plate appearance this
# many days old counts half as much as one from yesterday.
#
# The ROADMAP says "14-day weighted window" without naming a weighting, so this
# is a convention rather than a measurement, and it is exposed as a parameter for
# that reason. Seven days is chosen so the oldest plate appearance in a 14-day
# window carries roughly a quarter the weight of the newest, which is a visible
# gradient rather than a rounding difference: at a 14-day half-life the extremes
# differ by less than a factor of two and the weighting barely changes the answer,
# and at a 3-day half-life the window is effectively three days long and the other
# eleven are decoration. Issue #35's backtest is what should actually set it.
DEFAULT_HALF_LIFE_DAYS = 7.0

# The window `CALC_73` covers, from the ROADMAP.
TRENDING_WINDOW_DAYS = 14


def _probability(rate: Rate | None) -> PROBABILITY | None:
    return PROBABILITY(rate) if rate is not None else None


# ---------------------------------------------------------------------------
# CALC_72 -- season base rate
# ---------------------------------------------------------------------------


def calc_72_season_base_rate(
    pitches: Sequence[dict[str, Any]],
) -> PROBABILITY | None:
    """CALC_72: hits per plate appearance across the full season.

    The batter's own baseline, and the prior `CALC_75` shrinks toward. It is the
    largest sample any calculator in this model reports, which is exactly what
    makes it the right prior: the recent and matchup estimates are thin, and this
    is what they are thin *relative to*.

    Reduced through Category 8's `plate_appearances`, so it inherits that
    module's competitive-game filter and its `(game_pk, at_bat_number)` plate
    appearance identity. Reusing it rather than re-deriving is deliberate: two
    hit rates built on different denominator conventions would be blended
    together by `CALC_75` as though they measured the same thing, which is the
    `CALC_30` failure shape.

    **No Category 8 calculator reports this**, checked before it was written:
    `CALC_52` is a 3-game window, `CALC_53` 7 days and `CALC_54` 14 days. So
    unlike `CALC_73` this is not a redefinition of something already shipped.
    """
    pas = plate_appearances(pitches)
    hits = sum(1 for pa in pas if pa.get("events") in HIT_EVENTS)
    return _probability(rate_or_none(hits, len(pas)))


# ---------------------------------------------------------------------------
# CALC_73 -- trending short-window rate
# ---------------------------------------------------------------------------


def calc_73_trending_rate(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    window_days: int = TRENDING_WINDOW_DAYS,
) -> PROBABILITY | None:
    """CALC_73: recency-weighted hits per plate appearance over the last 14 days.

    Each plate appearance is weighted ``0.5 ** (age_in_days / half_life_days)``,
    so yesterday counts fully and the far edge of the window counts least. The
    rate is the weighted hit share; **the denominator is the plate appearance
    count, not the sum of the weights**, because the denominator has to be the
    sample size behind the rate for `CALC_75` to weight it correctly. That is the
    `CALC_30_COVERAGE` lesson, and it is easy to get wrong here because the
    weight sum is right there and looks like a denominator.

    **This overlaps `CALC_54` and the two must not both be blended.** `CALC_54`
    is the same 14-day window unweighted, so with `half_life_days` large enough
    the two converge exactly. Same double-count shape as `CALC_31` inside
    `CALC_32` and `CALC_34` inside `CALC_35`. `CALC_73` exists because the
    ROADMAP asks for the weighted form specifically; if a composite ever reads
    Category 8 directly it should take `CALC_54` or this, never both.

    Returns None when the window is empty, which for a hitter who has not played
    in two weeks is the honest answer rather than a zero rate.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")

    today = today or datetime.date.today()
    windowed = apply_window(plate_appearances(pitches), DAYS(window_days), today=today)
    if not windowed:
        return None

    weighted_hits = 0.0
    total_weight = 0.0
    for pa in windowed:
        played = _parse_date(pa.get("game_date"))
        if played is None:
            continue
        weight = 0.5 ** ((today - played).days / half_life_days)
        total_weight += weight
        if pa.get("events") in HIT_EVENTS:
            weighted_hits += weight

    if total_weight <= 0:
        return None
    return _probability(Rate(weighted_hits / total_weight, len(windowed)))


def _parse_date(value: Any) -> datetime.date | None:
    if isinstance(value, datetime.date):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# CALC_74 -- matchup-conditioned rate
# ---------------------------------------------------------------------------


def calc_74_matchup_rate(
    bvp: PROBABILITY | None = None,
    platoon: PROBABILITY | None = None,
    pitch_type: PROBABILITY | None = None,
) -> PROBABILITY | None:
    """CALC_74: hits per plate appearance conditioned on today's matchup.

    The ROADMAP names three conditions and this takes exactly three arguments,
    one per condition, each already a `p_hit`-scale rate from the category that
    owns it:

    - *bvp*: `CALC_01`, the pair's career head-to-head hit rate.
    - *platoon*: `CALC_09`, the hitter's season rate against that throwing hand.
    - *pitch_type*: one of `CALC_20` through `CALC_23`, the hitter's rate against
      the tier of stuff this starter throws.

    They are combined by precision weighting, each contributing in proportion to
    its own sample size, which is why every calculator in this model returns a
    denominator alongside a rate. A three-plate-appearance BvP history and a
    four-hundred-plate-appearance platoon split are both evidence and they are
    not equal evidence; averaging them flat would let the smallest sample in the
    model move the answer as much as the largest.

    **Takes only `PROBABILITY` values, and takes nothing from Category 7.** The
    tool is scoped to traditional starting pitchers, so the bullpen calculators
    are out. Passing a `MULTIPLIER` here would be the role-tag error #39 is open
    about: a whiff rate and a hit rate are both numbers near 0.3 and blending
    them silently produces something that is neither.

    Returns None when no component resolved, which is the common case early in a
    season for a pair that has never met and a hitter with no tier sample yet.
    """
    return _probability(_precision_weighted([bvp, platoon, pitch_type]))


def _precision_weighted(components: Sequence[PROBABILITY | None]) -> Rate | None:
    """Sample-size-weighted mean of *components*, or None if none resolved.

    The returned denominator is the total sample behind the mean, so the result
    composes: a precision-weighted mean of precision-weighted means gives the
    same answer as one flat pass over all the components.
    """
    total = 0
    weighted = 0.0
    for component in components:
        if component is None:
            continue
        rate = component.value
        if rate is None or rate.denominator <= 0:
            continue
        weighted += rate.rate * rate.denominator
        total += rate.denominator
    if total <= 0:
        return None
    return Rate(weighted / total, total)


# ---------------------------------------------------------------------------
# CALC_75 -- Bayesian composite
# ---------------------------------------------------------------------------


def calc_75_composite_hit_rate(
    season: PROBABILITY | None = None,
    trending: PROBABILITY | None = None,
    matchup: PROBABILITY | None = None,
    prior_strength: float | None = None,
) -> PROBABILITY | None:
    """CALC_75: posterior hit rate per plate appearance, blending 72, 73 and 74.

    A Beta-Binomial posterior with the season rate as the prior. Writing the
    prior as ``Beta(k*p72, k*(1-p72))`` and treating each remaining component as
    ``p_i * n_i`` hits in ``n_i`` plate appearances, the posterior mean is::

        (k * p72 + sum(p_i * n_i)) / (k + sum(n_i))

    which is a precision-weighted mean with the prior weighted at *k*.

    **`prior_strength` is the one number in this module that does not come from
    the data, and issue #34 owns it.** Its default is the season sample's own
    denominator, which makes this a plain sample-size-weighted mean of the three
    components and asserts nothing beyond what they already carry. A smaller *k*
    trusts recent and matchup form more; a larger one anchors harder to the
    season. There is no defensible way to pick it from inside this repo, because
    picking it well means measuring which choice predicts better, which is #35.

    **The season rate is the prior rather than a fourth equal component**, and
    that is a real choice. The alternative is shrinking toward a league mean,
    which is the textbook formulation and is unavailable: no league hit rate
    exists in this repo while #38 blocks the league-wide pull, and inventing one
    is what `CALC_34` refused to do. Using the hitter's own season line instead
    costs the between-player pooling a league prior would give and needs no
    constant from nowhere. It also degrades gracefully, since a hitter with a
    thin season line gets a weak prior automatically.

    Returns None when nothing resolved. A hitter with only a season rate returns
    that rate unchanged, which is correct: with no other evidence the posterior
    is the prior.
    """
    if season is None:
        return _probability(_precision_weighted([trending, matchup]))

    prior = season.value
    if prior is None or prior.denominator <= 0:
        return _probability(_precision_weighted([trending, matchup]))

    strength = prior.denominator if prior_strength is None else float(prior_strength)
    if strength < 0:
        raise ValueError("prior_strength must not be negative")

    weighted = prior.rate * strength
    total = strength
    for component in (trending, matchup):
        if component is None or component.value is None:
            continue
        rate = component.value
        if rate.denominator <= 0:
            continue
        weighted += rate.rate * rate.denominator
        total += rate.denominator

    if total <= 0:
        return None
    # The denominator is the evidence actually behind the posterior, so a caller
    # can tell a 600-plate-appearance answer from a 12-plate-appearance one. When
    # `prior_strength` is overridden it is a weight rather than a sample, so this
    # reports the real sample the prior came from, not the weight given to it.
    sample = prior.denominator + sum(
        c.value.denominator
        for c in (trending, matchup)
        if c is not None and c.value is not None and c.value.denominator > 0
    )
    return _probability(Rate(weighted / total, sample))


# ---------------------------------------------------------------------------
# CALC_76 -- final game hit probability
# ---------------------------------------------------------------------------


def projected_plate_appearances(
    lineup_spot_pa: EXPONENT | None,
    ninth_inning_risk: EXPONENT | None = None,
) -> float | None:
    """`PA_proj` for `CALC_76`, composed from Category 6's two `EXPONENT` keys.

    *lineup_spot_pa* is `CALC_41` and *ninth_inning_risk* is `CALC_45`, which is
    zero or negative. They are summed because that is what the roles mean: both
    adjust the exponent, and `CALC_45` is already signed.

    Composed here rather than reached for as a magic number. `main.py` currently
    estimates the exponent as ``pa / 5`` inside `binomial_probability`, which is
    a different quantity entirely (a rate of recent plate appearances, not a
    projection for today's game), and `CALC_76` must not inherit it.

    Returns None without a lineup spot, since a hitter with no posted spot has no
    projection. A missing ninth-inning term contributes 0.
    """
    if lineup_spot_pa is None or lineup_spot_pa.value is None:
        return None
    projected = lineup_spot_pa.value.rate
    if ninth_inning_risk is not None and ninth_inning_risk.value is not None:
        projected += ninth_inning_risk.value.rate
    return max(projected, 0.0)


def calc_76_game_hit_probability(
    composite: PROBABILITY | None,
    pa_projected: float | None,
) -> PROBABILITY | None:
    """CALC_76: ``P(>=1 hit) = 1 - (1 - p_hit) ** PA_proj``.

    The model's terminal output and the number the tool ranks on.

    **This is the one `PROBABILITY` in the model that must never be fed back into
    `p_hit`.** It is a per-game probability, not a per-plate-appearance rate, and
    blending it in would apply the binomial twice. `CALC_52` documents the same
    trap from the other end, where the ROADMAP's "hit in last 3 games (Y/N)" is
    this quantity arriving as an input. The role tag cannot express the
    difference, which is more evidence for the convention ruling #39 owes.

    The denominator is carried through from *composite*, so a caller can still
    tell a well-evidenced 0.72 from a thinly-evidenced one. A `PA_proj` of 0
    yields exactly 0.0, which is right: a hitter projected for no plate
    appearances cannot get a hit.

    Returns None when either input is missing, rather than substituting a default
    projection. A hitter with no posted lineup spot has no answer here.
    """
    if composite is None or composite.value is None or pa_projected is None:
        return None
    if pa_projected < 0:
        return None
    probability = 1.0 - (1.0 - composite.value.rate) ** pa_projected
    if math.isnan(probability):
        return None
    return _probability(Rate(probability, composite.value.denominator))


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_11(
    pitches: Sequence[dict[str, Any]] | None = None,
    bvp: PROBABILITY | None = None,
    platoon: PROBABILITY | None = None,
    pitch_type: PROBABILITY | None = None,
    lineup_spot_pa: EXPONENT | None = None,
    ninth_inning_risk: EXPONENT | None = None,
    today: datetime.date | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    prior_strength: float | None = None,
) -> dict[str, Any]:
    """Run every Category 11 calculator, wiring 72, 73 and 74 into 75 and 76.

    Absent input resolves every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_10`.

    *pitches* is the hitter's season Statcast pitch records, the same input
    Category 8 takes, and feeds `CALC_72` and `CALC_73`. *bvp*, *platoon* and
    *pitch_type* are `PROBABILITY` values from Categories 1, 2 and 3; see
    `calc_74_matchup_rate` for exactly which keys. *lineup_spot_pa* and
    *ninth_inning_risk* are Category 6's `CALC_41` and `CALC_45`.

    **This is the whole model in one call, and it is still not wired into a
    run.** `main.py` computes its ranking from `binomial_probability` as it
    always has. Switching the table over to `CALC_76` is a deliberate decision
    with no validation behind it yet, which is #35.
    """
    pitches = pitches or []

    season = calc_72_season_base_rate(pitches)
    trending = calc_73_trending_rate(pitches, today, half_life_days)
    matchup = calc_74_matchup_rate(bvp, platoon, pitch_type)
    composite = calc_75_composite_hit_rate(season, trending, matchup, prior_strength)
    projected = projected_plate_appearances(lineup_spot_pa, ninth_inning_risk)

    return {
        "CALC_72": season,
        "CALC_73": trending,
        "CALC_74": matchup,
        "CALC_75": composite,
        "CALC_76": calc_76_game_hit_probability(composite, projected),
        "PA_PROJ": projected,
    }
