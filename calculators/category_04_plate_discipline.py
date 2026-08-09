"""Category 4: Plate Discipline & Zone Location Profiles (`CALC_24`-`CALC_30`).

Pure functions over already-normalized pitch records; the network half lives in
`calculators.sources.category_04_plate_discipline`. See `ROADMAP.md` for the
source definition of each `CALC_NN`.

Every calculator here takes **both** sides and emits **two keys**, one per side,
rather than their product. The ROADMAP writes these as "Hitter Z-Contact % x
Pitcher Zone %", but the product of two unrelated rates is not a quantity
anything can read: an .85 zone-contact rate times a .45 zone rate is .38, which
is neither a contact rate nor a zone rate. Both terms survive, following the
`CALC_10` / `CALC_10_XBA` and `CALC_16` / `CALC_16_USAGE` precedent, and the
composite model weights them when issue #34 settles how.

**Read the role tags carefully.** Almost everything in this category is tagged
`MULTIPLIER`, and that tag is doing a narrower job here than its docstring
suggests. These are *skill rates*, not batting averages and not literal scaling
factors: a chase rate of .28, a whiff rate of .25, and a zone-contact rate of .85
all live on their own scales, and two of the three are bad news for the hitter
while the third is good news. `MULTIPLIER` is the available role that means "not
p_hit-scale, do not blend directly", which is the property that actually matters
at the call site. How each maps into `p_hit` is issue #39's question, and nothing
should multiply `p_hit` by one of these values in the meantime. `CALC_30` is the
sole `PROBABILITY` in the category, because a location-weighted xBA genuinely is
a batting-average-scale number.

**The count fields are pre-pitch.** `balls` and `strikes` describe the count the
pitch was thrown *into*, verified on the probe frames where every
`pitch_number == 1` row carried (0, 0). So `strikes == 2` selects pitches thrown
in a two-strike count, which is what CALC_29 wants, and it does not select the
pitch that produced the second strike.

**Per-pitch rates need no plate-appearance reduction.** Swing rate, whiff rate,
chase rate, and zone rate are all defined per pitch, so they are counted over
pitch rows directly. The one exception is CALC_29's pitcher side, a per-plate-
appearance out rate whose filter (`strikes == 2`) varies *within* a plate
appearance. That one reduces to the terminal pitch first, via
`common.terminal_pitch_by_pa`; filtering pitch rows first and grouping second is
the CALC_14 defect in issue #37. Because the count only ever climbs within a
plate appearance, the terminal pitch's `strikes` is also the maximum, so the
reduced row answers "did this plate appearance reach two strikes" exactly.
Confirmed on the probe frames: 489 of 489 plate appearances.

**Nothing here is wired into a run.** The calculators are built and tested in
isolation and get consumed compositely in a later release; the ranked table is
untouched and no Category 4 request is made during a daily run.
"""

from __future__ import annotations

from typing import Any, Sequence

from calculators.common import (
    CONTACT_DESCRIPTIONS,
    MULTIPLIER,
    ON_BASE_EVENTS,
    OUT_EVENTS,
    PROBABILITY,
    Rate,
    SWING_DESCRIPTIONS,
    WHIFF_DESCRIPTIONS,
    rate_or_none,
    terminal_pitch_by_pa,
)

# ---------------------------------------------------------------------------
# Pitch description vocabulary
# ---------------------------------------------------------------------------
#
# Statcast's `description` column is the only field that distinguishes a swing
# from a take, so every discipline rate in this category is built on these sets.
# The full vocabulary observed on the probe frames was `ball`, `blocked_ball`,
# `automatic_ball`, `called_strike`, `foul`, `foul_tip`, `foul_bunt`,
# `swinging_strike`, `swinging_strike_blocked`, `missed_bunt`, `hit_by_pitch`,
# `pitchout`, and `hit_into_play`.

CALLED_STRIKE_DESCRIPTIONS = frozenset({"called_strike"})

# Pitches that were never actually thrown. A pitch-timer violation advances the
# count without a delivery, so the batter had nothing to swing at and the pitcher
# nothing to locate. These rows carry a null `zone` (all 19 in the probe frames
# did), which already excludes them from every zone-based denominator, but the
# count-state rates in CALC_28 have no zone filter to hide behind and must
# exclude them by name. Four of the probe frames' automatic balls landed on
# `pitch_number == 1`, squarely inside CALC_28's population, so this is a real
# correction rather than a hypothetical one.
UNTHROWN_DESCRIPTIONS = frozenset({"automatic_ball", "automatic_strike"})

# ---------------------------------------------------------------------------
# Zone codes
# ---------------------------------------------------------------------------
#
# Statcast's own `zone`, used as published. Codes 1-9 are the 3x3 in-zone grid
# CALC_30 asks for; codes 11-14 are the four out-of-zone quadrants. There is no
# code 10. See the source module for why these are not reconstructed from
# `plate_x` / `plate_z`.

IN_ZONE_CODES = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})
OUT_OF_ZONE_CODES = frozenset({11, 12, 13, 14})


# ---------------------------------------------------------------------------
# Pitch-level predicates
# ---------------------------------------------------------------------------


def is_swing(pitch: dict[str, Any]) -> bool:
    """Whether the batter offered at *pitch*."""
    return pitch.get("description") in SWING_DESCRIPTIONS


def is_whiff(pitch: dict[str, Any]) -> bool:
    """Whether the batter swung at *pitch* and missed it entirely."""
    return pitch.get("description") in WHIFF_DESCRIPTIONS


def is_contact(pitch: dict[str, Any]) -> bool:
    """Whether the batter swung at *pitch* and made contact, fair or foul."""
    return pitch.get("description") in CONTACT_DESCRIPTIONS


def was_thrown(pitch: dict[str, Any]) -> bool:
    """Whether *pitch* was a real delivery rather than an automatic count event."""
    return pitch.get("description") not in UNTHROWN_DESCRIPTIONS


def zone_code(pitch: dict[str, Any]) -> int | None:
    """Statcast's zone code for *pitch* as an int, or None when untracked.

    Coerced rather than read straight through because the value arrives as a
    float whenever the source frame carried nulls in the column, which is the
    normal case: pandas widens an integer column to float64 to hold NaN, and the
    cache's CSV round-trip preserves that. A bare ``pitch["zone"] in
    IN_ZONE_CODES`` test would then compare 5.0 against a set of ints and match
    nothing, silently emptying every zone-based rate in the category.

    The coercion is integrality-checked rather than a plain ``int()``, which
    truncates. `zone` is categorical, so 9.7 is not a zone that rounds to a
    neighbor, it is a value this column should never hold; truncating it to 9
    would place a malformed reading inside the strike zone and let it into a rate.
    A non-integral value is no reading at all.

    Duck-typed rather than ``isinstance(raw, (int, float))`` on purpose: numpy's
    ``int64`` is not a subclass of Python's ``int``, and the source modules do not
    unwrap numpy scalars, so a type check would reject the ordinary case.
    ``OverflowError`` is caught alongside the rest because ``int(inf)`` raises it
    where ``int(nan)`` raises ``ValueError``.
    """
    raw = pitch.get("zone")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        code = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return code if code == raw else None


def is_in_zone(pitch: dict[str, Any]) -> bool:
    """Whether *pitch* was located in the strike zone."""
    return zone_code(pitch) in IN_ZONE_CODES


def is_out_of_zone(pitch: dict[str, Any]) -> bool:
    """Whether *pitch* was located outside the strike zone."""
    return zone_code(pitch) in OUT_OF_ZONE_CODES


def _zoned(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pitches carrying a usable zone reading.

    The denominator for every location share in this category. Not all pitches
    have one: pitch-timer violations were never thrown, and ordinary tracking
    gaps account for the rest (the probe frames carried a null zone on 3
    called strikes, 2 fouls, and 2 balls put in play). Counting those in a
    denominator would understate every location rate by the size of the gap
    rather than reporting a smaller, honest sample.
    """
    return [p for p in pitches if zone_code(p) is not None]


def _rate_over(
    pitches: Sequence[dict[str, Any]], numerator_test, denominator_test=None
) -> Rate | None:
    """Share of *pitches* passing *numerator_test*, among those passing the denominator.

    *denominator_test* defaults to every pitch. Returns None on an empty
    denominator, never a 0.0 rate, so no sample stays distinguishable from no
    occurrences.
    """
    population = (
        pitches
        if denominator_test is None
        else [p for p in pitches if denominator_test(p)]
    )
    if not population:
        return None
    return rate_or_none(
        sum(1 for p in population if numerator_test(p)), len(population)
    )


def _multiplier(rate: Rate | None) -> MULTIPLIER | None:
    return MULTIPLIER(rate) if rate is not None else None


# ---------------------------------------------------------------------------
# CALC_24 -- zone contact against zone occupancy
# ---------------------------------------------------------------------------


def calc_24_zone_contact_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_24: hitter Z-Contact %, and the starter's zone rate.

    `CALC_24` is contact per swing on pitches in the strike zone, the hitter's
    ability to put a bat on a strike. `CALC_24_ZONE_RATE` is the share of the
    starter's tracked pitches that are strikes by location.

    The two read in the same direction for the hitter, which is why they belong
    together: a strike-throwing starter matters more against a hitter who cannot
    square up strikes, and a hitter with elite zone contact is barely troubled by
    one. Neither is a hit rate; see the module docstring on the `MULTIPLIER` tag.
    """
    contact = _rate_over(
        hitter_pitches,
        is_contact,
        lambda p: is_in_zone(p) and is_swing(p),
    )
    zone_rate = _rate_over(_zoned(pitcher_pitches), is_in_zone)
    return {
        "CALC_24": _multiplier(contact),
        "CALC_24_ZONE_RATE": _multiplier(zone_rate),
    }


# ---------------------------------------------------------------------------
# CALC_25 -- chase vulnerability against pitches off the plate
# ---------------------------------------------------------------------------


def calc_25_chase_vulnerability(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_25: hitter chase rate, and the starter's rate of pitching out of the zone.

    `CALC_25` is swings per out-of-zone pitch seen, the standard O-Swing rate.
    `CALC_25_OZONE_RATE` is the share of the starter's tracked pitches located
    outside the zone.

    Both are inverted relative to CALC_24 in what they mean for the hitter: a
    high chase rate is a weakness, not a strength. The two zone rates are
    complements over the same denominator by construction, so `CALC_24_ZONE_RATE`
    and `CALC_25_OZONE_RATE` sum to one. Both are emitted anyway rather than one
    plus a subtraction, because a consumer reading `CALC_25` should not have to
    reach into another calculator's output to find the term that pairs with it.
    """
    chase = _rate_over(hitter_pitches, is_swing, is_out_of_zone)
    ozone_rate = _rate_over(_zoned(pitcher_pitches), is_out_of_zone)
    return {
        "CALC_25": _multiplier(chase),
        "CALC_25_OZONE_RATE": _multiplier(ozone_rate),
    }


# ---------------------------------------------------------------------------
# CALC_26 -- whiff overlay
# ---------------------------------------------------------------------------


def calc_26_whiff_overlay(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_26: hitter whiff rate per swing, and the starter's swinging-strike rate
    per pitch.

    **The two denominators differ on purpose.** `CALC_26` is whiffs per swing;
    `CALC_26_SWSTR` is swinging strikes per pitch thrown. That asymmetry is the
    ROADMAP's ("Hitter overall Whiff % x Pitcher overall Swing-and-Miss %") and it
    is also the industry convention, because the two answer different questions:
    whiff rate is a property of the hitter's swing, while swinging-strike rate
    folds in how often a pitcher can provoke a swing at all. A starter who
    generates chases is credited for it here, and would not be if both sides used
    a per-swing denominator. Anyone reconciling this module against Savant should
    expect the pitcher number to be roughly a third of the hitter number for that
    reason alone.

    Unthrown pitches are excluded from the pitcher's denominator: an automatic
    ball is not a pitch a hitter declined to miss.
    """
    whiff = _rate_over(hitter_pitches, is_whiff, is_swing)
    swstr = _rate_over(pitcher_pitches, is_whiff, was_thrown)
    return {
        "CALC_26": _multiplier(whiff),
        "CALC_26_SWSTR": _multiplier(swstr),
    }


# ---------------------------------------------------------------------------
# CALC_27 -- called-strike-plus-whiff interaction
# ---------------------------------------------------------------------------


def _csw_rate(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """Called strikes plus whiffs per pitch thrown.

    CSW deliberately pools two things a pitcher does not control equally: the
    umpire grants the called strike, the hitter donates the whiff. It is used
    anyway, here and league-wide, because the pooled rate stabilizes over a far
    smaller sample than either half. Per pitch on both sides, unlike CALC_26.
    """
    return _rate_over(
        pitches,
        lambda p: p.get("description") in CALLED_STRIKE_DESCRIPTIONS or is_whiff(p),
        was_thrown,
    )


def calc_27_csw_interaction(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_27: the hitter's CSW rate allowed, and the starter's CSW rate generated.

    `CALC_27` is the rate at which pitches to this hitter become called strikes
    or whiffs; `CALC_27_PITCHER_CSW` is the same rate for pitches this starter
    throws. Both per pitch, so they are directly comparable, unlike CALC_26's
    pair. A high value favors the pitcher on both sides.
    """
    return {
        "CALC_27": _multiplier(_csw_rate(hitter_pitches)),
        "CALC_27_PITCHER_CSW": _multiplier(_csw_rate(pitcher_pitches)),
    }


# ---------------------------------------------------------------------------
# CALC_28 -- first-pitch attack
# ---------------------------------------------------------------------------


def _first_pitches(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The thrown first pitch of each plate appearance.

    Selected by ``pitch_number == 1`` rather than by reducing plate appearances,
    because this is the one calculator that wants the *opening* pitch instead of
    the terminal one. That makes it a one-row-per-plate-appearance selection by
    construction, so it carries none of the terminal-attribution risk the rest of
    the module has to manage.

    Automatic balls are dropped here. A pitch-timer violation on 0-0 puts the
    count at 1-0 without a delivery, so counting it would charge the hitter a
    non-swing he was never offered and credit the pitcher a non-strike he never
    threw. Excluding it from both sides keeps the two rates measured over the
    same population.
    """
    return [p for p in pitches if p.get("pitch_number") == 1 and was_thrown(p)]


def calc_28_first_pitch_attack(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_28: hitter 0-0 swing rate, and the starter's first-pitch strike rate.

    `CALC_28_F_STRIKE` counts anything that is not a ball as a strike, which is
    the standard F-Strike definition and includes fouls and balls put in play.
    Statcast's `type` column carries exactly that trichotomy: ``B`` for a ball,
    ``S`` for any strike including a foul, and ``X`` for a ball in play. A hit
    batsman is typed ``B``, correctly.
    """
    swing_rate = _rate_over(_first_pitches(hitter_pitches), is_swing)
    f_strike = _rate_over(
        _first_pitches(pitcher_pitches), lambda p: p.get("type") in ("S", "X")
    )
    return {
        "CALC_28": _multiplier(swing_rate),
        "CALC_28_F_STRIKE": _multiplier(f_strike),
    }


# ---------------------------------------------------------------------------
# CALC_29 -- two-strike protection
# ---------------------------------------------------------------------------


def calc_29_two_strike_protection(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_29: hitter two-strike contact rate, and the starter's two-strike out rate.

    `CALC_29` is per pitch: contact per swing on pitches thrown in a two-strike
    count, the hitter's ability to stay alive.

    Expect it to run *above* the same hitter's overall contact rate, and do not
    read that as evidence of a defect. It was .764 against .694 overall on the
    smoke-test hitter, and the gap is structural: a foul with two strikes keeps
    the plate appearance alive at two strikes, so a hitter who fouls off six
    pitches contributes six two-strike contacts to this denominator and nothing to
    the whiff count. The rate is self-weighted toward hitters who battle. That is
    inherent to the per-pitch definition the ROADMAP asks for, not a correctable
    bias, but a consumer comparing it against CALC_24 needs to know the two are
    not on the same footing.

    `CALC_29_PITCHER_OUT` is per plate appearance: the share of plate appearances
    reaching two strikes that ended with the batter retired. It is the one rate
    in this category that must reduce to the terminal pitch before filtering.
    ``strikes`` changes within a plate appearance, so filtering pitch rows on
    ``strikes == 2`` and then grouping would hold onto plate appearances whose
    outcome is recorded on an earlier, discarded row. Because the count only
    climbs, the terminal pitch's ``strikes`` is the plate appearance's maximum, so
    testing the reduced row for ``strikes == 2`` is exactly the question "did this
    plate appearance reach two strikes".

    Outcomes are classified against the explicit `OUT_EVENTS` and
    `ON_BASE_EVENTS` sets, and a plate appearance whose terminal event falls in
    neither is dropped from both numerator and denominator. Statcast records
    events like ``truncated_pa`` and ``caught_stealing_2b`` that end a plate
    appearance without resolving the batter, and treating "not on base" as "out"
    would score every one of them as a pitcher success.
    """
    contact = _rate_over(
        hitter_pitches,
        is_contact,
        lambda p: p.get("strikes") == 2 and is_swing(p),
    )

    two_strike_pas = [
        p for p in terminal_pitch_by_pa(pitcher_pitches) if p.get("strikes") == 2
    ]
    resolved = [
        p
        for p in two_strike_pas
        if p.get("events") in OUT_EVENTS or p.get("events") in ON_BASE_EVENTS
    ]
    out_rate = (
        rate_or_none(
            sum(1 for p in resolved if p.get("events") in OUT_EVENTS), len(resolved)
        )
        if resolved
        else None
    )

    return {
        "CALC_29": _multiplier(contact),
        "CALC_29_PITCHER_OUT": _multiplier(out_rate),
    }


# ---------------------------------------------------------------------------
# CALC_30 -- quadrant location acuity
# ---------------------------------------------------------------------------


def hitter_zone_xba(hitter_pitches: Sequence[dict[str, Any]]) -> dict[int, Rate]:
    """Mean expected batting average per in-zone quadrant, keyed by zone code.

    One entry per zone 1-9 in which the hitter put a ball in play. The
    denominator is batted balls, not pitches: an xBA estimate only exists where
    the ball was struck, so any other denominator would misreport what was
    measured. Zones with no batted ball are absent rather than zero.

    Expect thin samples. A regular hitter's full season yielded 5 to 25 batted
    balls per zone on the probe frame, which is why the Rate carries its
    denominator and why shrinkage (#34) matters more here than almost anywhere
    else in the model.
    """
    totals: dict[int, list[float]] = {}
    for pitch in hitter_pitches:
        code = zone_code(pitch)
        if code not in IN_ZONE_CODES:
            continue
        xba = pitch.get("estimated_ba_using_speedangle")
        if xba is None:
            continue
        totals.setdefault(code, []).append(float(xba))
    return {
        code: Rate(sum(values) / len(values), len(values))
        for code, values in totals.items()
    }


def pitcher_zone_weights(pitcher_pitches: Sequence[dict[str, Any]]) -> dict[int, float]:
    """The starter's location distribution across zones 1-9, summing to one.

    Restricted to in-zone pitches and renormalized over them, so this is "where
    in the zone he works" rather than "how often he is in the zone". The latter
    is CALC_24_ZONE_RATE's job, and folding both into one number would make a
    wild starter look like a corner painter.
    """
    in_zone = [p for p in pitcher_pitches if is_in_zone(p)]
    if not in_zone:
        return {}
    weights: dict[int, float] = {}
    for pitch in in_zone:
        code = zone_code(pitch)
        weights[code] = weights.get(code, 0.0) + 1.0
    return {code: count / len(in_zone) for code, count in weights.items()}


def calc_30_quadrant_acuity(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_30: the hitter's zone xBA profile weighted by where the starter lives.

    `CALC_30` is the sum over zones of (starter's share of in-zone pitches in
    that zone) x (hitter's xBA in that zone), renormalized over the zones where
    the hitter actually has a batted-ball sample. Renormalizing is what makes the
    output a batting-average-scale `PROBABILITY` rather than a number scaled down
    by however much of the heatmap happened to be uncovered.

    **That renormalization is also the trap, and `CALC_30_COVERAGE` exists to
    expose it.** A hitter with one batted ball in one zone, if that zone is where
    the starter works, yields a fully confident-looking weighted xBA computed
    from a single reading. `CALC_30_COVERAGE` reports the share of the *starter's*
    in-zone pitches that fall in zones the hitter has covered, which is the
    honest statement of how much of the real heatmap the weighted average
    represents. It is weighted by the starter's own distribution rather than
    counted as covered-zones-over-nine, because missing the zone he pounds is not
    the same problem as missing one he never touches.

    Both denominators are the sample size behind their own rate, the way every
    other `Rate` in the model carries one, so #34's shrinkage can read them
    without a special case. `CALC_30`'s is the total batted balls the weighted
    average was computed from. `CALC_30_COVERAGE`'s is the starter's in-zone pitch
    count, because coverage is a share of *his pitches*; the number of distinct
    zones he happened to touch would be a different quantity wearing the same
    slot.
    """
    zone_xba = hitter_zone_xba(hitter_pitches)
    weights = pitcher_zone_weights(pitcher_pitches)
    if not zone_xba or not weights:
        return {"CALC_30": None, "CALC_30_COVERAGE": None}

    in_zone_pitches = sum(1 for p in pitcher_pitches if is_in_zone(p))
    covered = weights.keys() & zone_xba.keys()
    covered_weight = sum(weights[code] for code in covered)
    coverage = Rate(covered_weight, in_zone_pitches)
    if not covered_weight:
        return {"CALC_30": None, "CALC_30_COVERAGE": MULTIPLIER(coverage)}

    weighted = sum(weights[code] * zone_xba[code].rate for code in covered)
    batted_balls = sum(zone_xba[code].denominator for code in covered)
    return {
        "CALC_30": PROBABILITY(Rate(weighted / covered_weight, batted_balls)),
        "CALC_30_COVERAGE": MULTIPLIER(coverage),
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_04(
    hitter_pitches: Sequence[dict[str, Any]] | None = None,
    pitcher_pitches: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run every Category 4 calculator.

    Absent inputs resolve every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_03` so a
    consumer can rely on the key set without knowing what was fetched.

    Both arguments are pitch-record sequences from
    `calculators.sources.category_04_plate_discipline`: `hitter_pitches` is every
    pitch the batter saw this season, `pitcher_pitches` every pitch the starter
    threw.
    """
    hitter_pitches = hitter_pitches or []
    pitcher_pitches = pitcher_pitches or []

    results: dict[str, Any] = {}
    for calculator in (
        calc_24_zone_contact_match,
        calc_25_chase_vulnerability,
        calc_26_whiff_overlay,
        calc_27_csw_interaction,
        calc_28_first_pitch_attack,
        calc_29_two_strike_protection,
        calc_30_quadrant_acuity,
    ):
        results.update(calculator(hitter_pitches, pitcher_pitches))
    return results
