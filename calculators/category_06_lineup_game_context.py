"""Category 6: Lineup & Game Context Factors (`CALC_41`-`CALC_46`).

Pure functions; no network. The network half lives in
`calculators.sources.category_06_lineup_game_context`. See `ROADMAP.md` for the
source definition of each `CALC_NN`.

`CALC_41` shipped first and alone; `CALC_42`-`CALC_46` complete the category.
The module's original framing still holds: this is where quantities that change
**how many plate appearances a hitter gets, and against whom** live, which is why
it owns the only two `EXPONENT` calculators in the model.

**What this category can and cannot reach.** `CALC_46` wants a Vegas game run
total. There is no source for it here, and there is no source for the league mean
it would be measured against either, so it ships as a pure function over two
caller-supplied numbers and returns None until something provides them. Naming a
plausible-looking league average would be inventing exactly the kind of constant
`CALC_34` refused to invent.

**Times through order is reconstructed, and the two sides need different rules.**
Statcast publishes no TTO column and there is no `sitCodes` value for it either
(checked: the situation vocabulary has inning codes and pitch-count buckets, and
nothing for times through the order). See `calc_44_times_through_order` for why
the pitcher-side and batter-side reconstructions cannot share an identification
rule.

**Nothing here is wired into a run.** `main.binomial_probability` still computes
its exponent as `pa / 5`, and the ranked table is unchanged.
"""

from __future__ import annotations

from typing import Any, Sequence

from calculators.common import (
    DELTA,
    EXPONENT,
    HIT_EVENTS,
    MULTIPLIER,
    PROBABILITY,
    Rate,
    is_competitive,
    rate_or_none,
    terminal_pitch_by_pa,
)

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


# Number of spots in a batting order. Used to wrap neighbour lookups: the hitter
# batting before the leadoff man is the ninth hitter, not nobody.
LINEUP_SPOTS = 9

# Times through the order this model reports. A fourth pass happens, but rarely
# enough that its sample is noise (2 of a probe hitter's 304 plate appearances
# against starters), and a starter who reaches it is by definition cruising.
TTO_BUCKETS = (1, 2, 3)

# Latest inning in which a batter's first plate appearance can still identify the
# opposing starter. See `batter_tto` for why this guard exists.
MAX_STARTER_FIRST_INNING = 3


# ---------------------------------------------------------------------------
# CALC_42 / CALC_43 -- lineup neighbours
# ---------------------------------------------------------------------------


def neighbour_spots(lineup_spot: int | None) -> tuple[int, int] | None:
    """``(preceding, succeeding)`` batting-order spots, wrapping at the ends.

    The wrap is the point: the hitter batting before the leadoff man is the ninth
    hitter, and the hitter on deck behind the ninth is the leadoff man. Treating
    either as absent would blank `CALC_42` for every leadoff hitter and `CALC_43`
    for every ninth hitter, which is a fifth of the pool.
    """
    if lineup_spot not in range(1, LINEUP_SPOTS + 1):
        return None
    preceding = lineup_spot - 1 or LINEUP_SPOTS
    succeeding = lineup_spot % LINEUP_SPOTS + 1
    return preceding, succeeding


def _rate_from_line(line: dict[str, Any] | None, field: str) -> Rate | None:
    """Read one season-rate field off a hitter's line, with its PA behind it.

    The MLB Stats API serves `obp`, `slg` and `ops` as strings (".375"), and
    serves ".---" for a player with no plate appearances, which floats cleanly to
    nothing. A value that will not parse is no reading rather than a zero.
    """
    if not line:
        return None
    raw = line.get(field)
    plate_appearances = line.get("plateAppearances") or 0
    if raw is None or not plate_appearances:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return Rate(value, int(plate_appearances))


def calc_42_preceding_batter_obp(
    lineup: dict[int, dict[str, Any]] | None,
    lineup_spot: int | None,
) -> MULTIPLIER | None:
    """CALC_42: the on-base ability of the hitter batting immediately before.

    *lineup* maps batting-order spot to that hitter's season line. A high
    preceding on-base percentage means this hitter more often comes up with a
    runner aboard, which puts the pitcher in the stretch and the defense out of
    position.

    **The ROADMAP names two mechanisms and this calculator models one.** "Pitcher
    forced into stretch" acts on *this* hitter's chance of a hit and is what the
    value reports. "More PAs" is a team-level effect on how deep the lineup bats,
    which is not a property of one hitter's predecessor and belongs to the
    `PA_proj` side that `CALC_41` and `CALC_45` own. Reporting the same on-base
    number under both lanes would invite the composite model to count it twice.

    `MULTIPLIER`, not `PROBABILITY`: an OBP near .340 is on a different scale
    from a per-plate-appearance hit rate near .230, and it describes a different
    player. The denominator is the *predecessor's* plate appearances, which is
    the sample the reading rests on.
    """
    spots = neighbour_spots(lineup_spot)
    if spots is None or not lineup:
        return None
    rate = _rate_from_line(lineup.get(spots[0]), "obp")
    return MULTIPLIER(rate) if rate is not None else None


def calc_43_succeeding_batter_protection(
    lineup: dict[int, dict[str, Any]] | None,
    lineup_spot: int | None,
) -> dict[str, Any]:
    """CALC_43: the threat posed by the on-deck hitter.

    A dangerous hitter on deck is why a pitcher attacks the current one rather
    than pitching around him, so protection raises the chance of a hittable
    pitch.

    **Two keys, because the ROADMAP does not say what "threat" means.** `CALC_43`
    is the on-deck hitter's OPS, the conventional single-number answer;
    `CALC_43_SLG` is his slugging alone, which is arguably the better read of
    protection since a pitcher fears extra-base damage rather than walks and OPS
    folds on-base ability back in. Both arrive in the same response, so the second
    key costs nothing, and this follows `CALC_06` and `CALC_55`, which likewise
    emit two named quantities where the ROADMAP named one ambiguous one. Issue #39
    rules on which the composite model uses.
    """
    spots = neighbour_spots(lineup_spot)
    line = lineup.get(spots[1]) if spots is not None and lineup else None
    result = {}
    for key, field in (("CALC_43", "ops"), ("CALC_43_SLG", "slg")):
        rate = _rate_from_line(line, field)
        result[key] = MULTIPLIER(rate) if rate is not None else None
    return result


# ---------------------------------------------------------------------------
# CALC_44 -- times through the order
# ---------------------------------------------------------------------------


def _plate_appearances(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Competitive plate appearances, oldest first, one record each.

    Spring training is excluded for the reason every recency-aware category
    excludes it (issue #42), and the terminal-pitch reduction comes first, since
    times through the order is an attribute of a plate appearance rather than of
    a pitch.
    """
    terminal = [p for p in terminal_pitch_by_pa(pitches) if is_competitive(p)]
    return sorted(
        terminal, key=lambda p: (p.get("game_pk") or 0, p.get("at_bat_number") or 0)
    )


def _hit_rate_by_bucket(
    labelled: Sequence[tuple[int, dict[str, Any]]],
) -> dict[int, Rate]:
    """Hits per plate appearance within each times-through-order bucket."""
    buckets: dict[int, list[dict[str, Any]]] = {}
    for tto, pa in labelled:
        if tto in TTO_BUCKETS:
            buckets.setdefault(tto, []).append(pa)
    rates = {}
    for tto, pas in buckets.items():
        hits = sum(1 for pa in pas if pa.get("events") in HIT_EVENTS)
        rate = rate_or_none(hits, len(pas))
        if rate is not None:
            rates[tto] = rate
    return rates


def pitcher_tto(pitches: Sequence[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """Label a pitcher's plate appearances with times through the order.

    Two rules, and both matter.

    **Relief outings are dropped**, reusing Category 9's identification: a start
    is one where the pitcher's earliest plate appearance in the game came in
    inning 1 with zero outs. Every plate appearance of a relief outing would
    otherwise land in bucket 1, since a reliever rarely faces the same hitter
    twice, dragging bucket 1 toward bullpen quality and inflating the apparent
    penalty. It also matches what the calculator is *for*: the starter a hitter is
    scheduled to face.

    **The bucket counts repeat encounters with the same batter**, not
    ``ceil(index / 9)``. The two agree on 98.9 percent of the probe pitcher's 440
    plate appearances, and every one of the 5 disagreements is a substitution,
    where counting encounters is right and counting lineup passes is wrong.
    """
    pas = _plate_appearances(pitches)
    firsts: dict[Any, dict[str, Any]] = {}
    for pa in pas:
        firsts.setdefault(pa.get("game_pk"), pa)
    started = {
        game_pk
        for game_pk, first in firsts.items()
        if first.get("inning") == 1 and not (first.get("outs_when_up") or 0)
    }

    seen: dict[tuple[Any, Any], int] = {}
    labelled = []
    for pa in pas:
        game_pk = pa.get("game_pk")
        if game_pk not in started:
            continue
        key = (game_pk, pa.get("batter"))
        seen[key] = seen.get(key, 0) + 1
        labelled.append((seen[key], pa))
    return labelled


def batter_tto(pitches: Sequence[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """Label a batter's plate appearances with times through the order.

    **Category 9's start test cannot be reused here, and using it silently
    destroys the sample.** That test asks whether a pitcher's earliest plate
    appearance in a game came in inning 1 with nobody out. On a *pitcher's* frame
    that works, because the frame holds every batter he faced. A *batter's* frame
    holds only plate appearances involving that batter, so a pitcher's earliest
    row in it is the first time he faced *this hitter*, which for a starter is
    usually the second inning or later. Applying the test anyway classified 35 of
    one probe hitter's 261 plate appearances as facing a starter, when the true
    share is around 60 percent.

    The rule used instead runs the other way round: the starter is whoever the
    batter faced in **his own first plate appearance of the game**. That is exact
    for anyone in the posted lineup. The guard is `MAX_STARTER_FIRST_INNING`,
    which drops a game the hitter entered late: a pinch hitter debuting in the
    ninth would otherwise crown a reliever as the starter. Measured across two
    probe hitters, the first plate appearance fell in inning 1 in 170 of 171
    games, the exception being exactly such a ninth-inning appearance, and the
    surviving share of plate appearances was 60 and 61 percent.

    **This leg carries a survivorship confound that the pitcher leg does not, and
    it runs the opposite way.** A hitter only reaches bucket 3 when the starter
    was going well enough to still be in the game, so the batter-side rate tends
    to *fall* across buckets (.254/.148/.136 and .225/.214/.193 on the two probe
    hitters) while the pitcher side rises. Read as "how this hitter does against a
    starter who has lasted", not as this hitter's own fatigue curve.
    """
    pas = _plate_appearances(pitches)
    starters: dict[Any, Any] = {}
    for pa in pas:
        game_pk = pa.get("game_pk")
        if game_pk in starters:
            continue
        inning = pa.get("inning")
        if inning is not None and inning <= MAX_STARTER_FIRST_INNING:
            starters[game_pk] = pa.get("pitcher")

    seen: dict[Any, int] = {}
    labelled = []
    for pa in pas:
        game_pk = pa.get("game_pk")
        if game_pk not in starters or pa.get("pitcher") != starters[game_pk]:
            continue
        seen[game_pk] = seen.get(game_pk, 0) + 1
        labelled.append((seen[game_pk], pa))
    return labelled


def calc_44_times_through_order(
    pitcher_pitches: Sequence[dict[str, Any]] | None = None,
    batter_pitches: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """CALC_44: hit rate by times through the order, both sides, plus the penalty.

    Seven keys. `CALC_44_PITCHER_TTO1` through `_TTO3` are the starter's hit rate
    allowed per plate appearance in each pass; `CALC_44_BATTER_TTO1` through
    `_TTO3` are the hitter's own. All six are genuine `PROBABILITY` values, on the
    model's own per-plate-appearance scale.

    `CALC_44_PENALTY` is the pitcher's third-pass rate minus his first, the
    "vulnerability" the ROADMAP names, as a `DELTA` because it is signed and a
    pitcher who holds up is a real negative reading rather than a missing one. It
    is None whenever either leg is, since a difference against a missing baseline
    is not a small number.

    The probe starter ran .251 / .270 / .337 across the three passes, a +.085
    penalty, which is the classic shape.
    """
    pitcher_rates = _hit_rate_by_bucket(pitcher_tto(pitcher_pitches or []))
    batter_rates = _hit_rate_by_bucket(batter_tto(batter_pitches or []))

    result: dict[str, Any] = {}
    for tto in TTO_BUCKETS:
        for prefix, rates in (
            ("CALC_44_PITCHER", pitcher_rates),
            ("CALC_44_BATTER", batter_rates),
        ):
            rate = rates.get(tto)
            result[f"{prefix}_TTO{tto}"] = (
                PROBABILITY(rate) if rate is not None else None
            )

    first, third = pitcher_rates.get(1), pitcher_rates.get(3)
    result["CALC_44_PENALTY"] = (
        DELTA(Rate(third.rate - first.rate, third.denominator))
        if first is not None and third is not None
        else None
    )
    return result


# ---------------------------------------------------------------------------
# CALC_45 -- the skipped bottom of the ninth
# ---------------------------------------------------------------------------


def calc_45_bottom_ninth_pa_risk(
    batter_is_home: bool | None,
    game_context: dict[str, Any] | None = None,
) -> EXPONENT | None:
    """CALC_45: plate appearances a home hitter expects to lose to a skipped ninth.

    Negative, in plate appearances, and `EXPONENT` because it adjusts `PA_proj`
    rather than `p_hit`. This is the only Category 6 calculator besides `CALC_41`
    that touches the exponent, which is the lane this module owns.

    **An away hitter returns exactly 0.0, which is a measurement and not a
    missing value.** The top of the ninth is always played, so a road hitter's
    plate-appearance projection is genuinely unaffected. Returning None would
    make "no risk" indistinguishable from "no data".

    The ROADMAP states the loss conditionally, as 0.5 plate appearances when the
    home team is leading. A projection cannot condition on that, since at pick
    time nobody knows who will be ahead, so the value is the unconditional
    expectation: 0.5 multiplied by how often the home half is never played.
    That rate is measured, not assumed, over 780 of 1,761 completed
    regular-season games reaching nine innings (44.3 percent), and lives in
    `calculators/data/league_game_context.json`.

    **Deliberately not spot-dependent**, unlike `CALC_41` in this same module. A
    skipped ninth removes roughly one lineup turn's worth of plate appearances,
    and which spots lose them depends on where the order happens to stand after
    eight innings, which is close to uniform across games. Averaged over a
    season every spot loses about the same, so a per-spot refinement would add
    precision the underlying data does not have.
    """
    if batter_is_home is None:
        return None
    if not batter_is_home:
        return EXPONENT(Rate(0.0, 1))

    context = game_context or {}
    skipped = (context.get("skipped_ninth") or {}).get("rate")
    lost = context.get("pa_lost_per_skipped_ninth")
    if skipped is None or lost is None:
        return None
    denominator = (context.get("skipped_ninth") or {}).get("denominator") or 1
    return EXPONENT(Rate(-float(skipped) * float(lost), int(denominator)))


# ---------------------------------------------------------------------------
# CALC_46 -- expected scoring environment
# ---------------------------------------------------------------------------


def calc_46_expected_scoring(
    run_total: float | None = None,
    league_mean_total: float | None = None,
) -> MULTIPLIER | None:
    """CALC_46: today's Vegas run total against the league mean total.

    **Returns None in every current code path, and that is the honest state.**
    The ROADMAP asks for a betting market's projected total runs as a proxy for
    the hitting environment. Neither number is reachable: the MLB Stats API
    publishes no betting lines, pybaseball exposes none, and there is no source
    for the league mean of a quantity that cannot be fetched in the first place.

    It ships as a signature rather than being omitted so that the shape is
    recorded and an odds feed plugs straight in. What it deliberately does not do
    is default *league_mean_total* to a plausible-looking 8.5. An invented
    reference would make every game look like a measured deviation from a real
    league average, which is precisely the constant-from-nowhere `CALC_34`
    refused to invent.

    The denominator is 1 and is not a sample size.
    """
    if run_total is None or not league_mean_total:
        return None
    return MULTIPLIER(Rate(float(run_total) / float(league_mean_total), 1))


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_06(
    lineup_spot: int | None = None,
    lineup: dict[int, dict[str, Any]] | None = None,
    pitcher_pitches: Sequence[dict[str, Any]] | None = None,
    batter_pitches: Sequence[dict[str, Any]] | None = None,
    batter_is_home: bool | None = None,
    game_context: dict[str, Any] | None = None,
    run_total: float | None = None,
    league_mean_total: float | None = None,
) -> dict[str, Any]:
    """Run every Category 6 calculator.

    Absent inputs resolve to ``None`` rather than omitting the key, matching the
    contract of `compute_category_01` through `compute_category_05`.

    *lineup* maps batting-order spot to that hitter's season line, from
    `calculators.sources.category_06_lineup_game_context.fetch_lineup_rates`.
    *game_context* is the table loaded by `calculators.baselines`.
    """
    results: dict[str, Any] = {
        "CALC_41": calc_41_lineup_spot_pa_expectation(lineup_spot),
        "CALC_42": calc_42_preceding_batter_obp(lineup, lineup_spot),
        "CALC_45": calc_45_bottom_ninth_pa_risk(batter_is_home, game_context),
        "CALC_46": calc_46_expected_scoring(run_total, league_mean_total),
    }
    results.update(calc_43_succeeding_batter_protection(lineup, lineup_spot))
    results.update(calc_44_times_through_order(pitcher_pitches, batter_pitches))
    return results
