"""Category 3 — Pitch Arsenal & Movement Matchups (`CALC_16`-`CALC_23`).

Pure functions over already-normalized pitch records; the network half lives in
`calculators.sources.category_03_pitch_arsenal`. See `ROADMAP.md` for the source
definition of each `CALC_NN`.

Every calculator here is a genuine cross-product and takes **both** sides:

- **pitcher rows** establish what today's starter throws — his arsenal mix, his
  fastball velocity, his approach angle, his break, his extension.
- **hitter rows** establish how this hitter fares against that class of pitch,
  averaged over everyone who threw it to him.

The hitter side is deliberately not restricted to this starter. Category 1
already owns the head-to-head question and returns nothing when the pair has
never met; Category 3 answers the wider one, which is why it still has a sample
for a hitter facing a rookie.

**Terminal-pitch attribution.** Category 2 filters pitch rows on attributes that
are constant within a plate appearance (the pitcher's hand, his arm slot), so it
can group every matching pitch and read the PA's outcome off whichever row
carried it. Category 3 filters on attributes that *vary* pitch to pitch — a PA
can see a 96 mph fastball and an 84 mph slider — so a rate over "PAs containing a
matching pitch" would credit the outcome to a pitch that may not have produced
it. `terminal_pitch_by_pa` instead reduces each PA to the single pitch that
ended it, and the tier filters run over those. Verified against Statcast: the
maximum-`pitch_number` row of every plate appearance is exactly the row carrying
a terminal `events` value (240 of 240 PAs in the probe sample, 2026-08-08).

Expected-stat and run-value calculators are exempt — xBA exists per batted ball
and `delta_run_exp` per pitch, so both are naturally per-pitch and need no PA
reduction.

**Rates are per plate appearance, not per at-bat**, for the same reason
Categories 1 and 2 are: the model's foundation defines `p_hit` per plate
appearance and combines it as ``1 - (1 - p_hit)^PA_proj``.

**Nothing here is wired into a run.** The calculators are built and tested in
isolation and get consumed compositely in a later release; the ranked table is
untouched and no Category 3 request is made during a daily run.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Sequence

from calculators.common import (
    DELTA,
    PITCH_CLASS_BREAKING,
    PITCH_CLASS_FASTBALL,
    PITCH_CLASS_OFFSPEED,
    HIT_EVENTS,
    MULTIPLIER,
    PROBABILITY,
    Rate,
    pitch_class,
    rate_or_none,
    terminal_pitch_by_pa,
)

# ---------------------------------------------------------------------------
# Tier boundaries
# ---------------------------------------------------------------------------
#
# Every constant below was measured against the same league sample: 20,545
# pitches seen by 12 regular hitters across both leagues over the 2026 season
# through 2026-08-07. A hitter's season is a naturally league-representative
# sample of pitches — it is drawn from every staff he faced, and weighted by how
# often hitters actually see each kind. Re-derive these if the league's pitch mix
# shifts; they describe a distribution, not a rule of the game.

# CALC_20 — velocity brackets, in mph. Taken verbatim from the ROADMAP rather
# than balanced, because these are the brackets it specifies. They are lopsided
# against the measured sample (58 percent under 92, 28 percent between, 14
# percent above), which is a property of the game: most pitches thrown are not
# fastballs.
VELOCITY_TIER_BOUNDS_MPH = (92.0, 96.0)

VELOCITY_TIER_UNDER_92 = "under_92"
VELOCITY_TIER_92_TO_96 = "92_to_96"
VELOCITY_TIER_OVER_96 = "over_96"

# CALC_21 — vertical approach angle, in degrees, negative for a descending
# pitch. Tertile-balanced over *fastballs only* (FF/SI/FC, n=11,163; median
# -5.17, tertiles -5.69 and -4.69).
#
# Restricting to fastballs is the whole point. Pooled across all pitch types VAA
# mostly measures arsenal mix rather than delivery — the league spread runs from
# -4.63 for four-seamers to -9.52 for curveballs, so a starter who throws many
# curves would grade "steep" on his pitch selection alone. Within the fastball
# family the spread is a single degree, and that degree is the actual "flat vs.
# steep fastball" property the calculator is after.
FASTBALL_VAA_TIER_BOUNDS_DEGREES = (-5.69, -4.69)

VAA_TIER_STEEP = "steep"
VAA_TIER_AVERAGE = "average"
VAA_TIER_FLAT = "flat"

# CALC_22 — horizontal movement threshold, **in feet**. Statcast reports `pfx_x`
# in feet; Baseball Savant displays the same quantity in inches, so this is a
# 12x error waiting to happen and the unit is in the name on purpose. 1.25 ft is
# 15 inches, the ~79th percentile of the measured sample.
#
# Magnitude, not direction: a sweeper and a sinker are the ROADMAP's two named
# examples and they break in opposite directions, so the plain reading of
# "extreme horizontal movement" is |pfx_x|. If direction ever matters — glove
# side away from a same-handed hitter is not the same pitch as arm side in on
# him — Statcast carries `api_break_x_arm` and `api_break_x_batter_in` for it.
EXTREME_PFX_X_FEET = 1.25

# CALC_23 — release extension, in feet. Tertile-balanced (n=20,309; median 6.4,
# tertiles 6.3 and 6.6). The spread is genuinely this tight; a third of a foot
# separates the short third of the league from the long third.
EXTENSION_TIER_BOUNDS_FEET = (6.3, 6.6)

EXTENSION_TIER_SHORT = "short"
EXTENSION_TIER_AVERAGE = "average"
EXTENSION_TIER_LONG = "long"

# Geometry for the vertical-approach-angle solution. Statcast's release-point
# tracking is anchored 50 feet from home plate, and the front edge of the plate
# is 17 inches from its point.
_VAA_RELEASE_Y_FEET = 50.0
_VAA_PLATE_FRONT_Y_FEET = 17.0 / 12.0


# ---------------------------------------------------------------------------
# Pitch-level helpers
# ---------------------------------------------------------------------------


def velocity_tier(release_speed: float | None) -> str | None:
    """Bucket a release speed into the ROADMAP's three velocity brackets."""
    if release_speed is None:
        return None
    low, high = VELOCITY_TIER_BOUNDS_MPH
    if release_speed < low:
        return VELOCITY_TIER_UNDER_92
    if release_speed <= high:
        return VELOCITY_TIER_92_TO_96
    return VELOCITY_TIER_OVER_96


def vertical_approach_angle(pitch: dict[str, Any]) -> float | None:
    """Vertical approach angle at the front of the plate, in degrees.

    Solved from the release-point velocity and acceleration vectors, which is
    the only way to get it — Statcast publishes the trajectory terms but not the
    angle. Negative means descending, which is nearly every pitch ever thrown.

    Returns None when any trajectory term is missing, or when the geometry does
    not resolve (a non-negative discriminant is required for the square root,
    and `ay` appears in a denominator). A malformed row yields no reading rather
    than an exception.

    Verified against the measured league sample: four-seamers came out flattest
    at -4.63 degrees and curveballs steepest at -9.52, with sinkers, cutters,
    and the breaking balls ordered correctly in between. An inverted sign or a
    dropped `ay` term would have broken that ordering.
    """
    try:
        vy0 = pitch["vy0"]
        ay = pitch["ay"]
        vz0 = pitch["vz0"]
        az = pitch["az"]
    except (KeyError, TypeError):
        return None
    if None in (vy0, ay, vz0, az) or not ay:
        return None

    distance = _VAA_RELEASE_Y_FEET - _VAA_PLATE_FRONT_Y_FEET
    discriminant = vy0**2 - 2 * ay * distance
    if discriminant < 0:
        return None

    # The ball travels toward the plate, i.e. in the negative y direction.
    vy_plate = -math.sqrt(discriminant)
    flight_time = (vy_plate - vy0) / ay
    vz_plate = vz0 + az * flight_time
    if not vy_plate:
        return None
    return -math.degrees(math.atan(vz_plate / vy_plate))


def fastball_vaa_tier(vaa: float | None) -> str | None:
    """Bucket a fastball's vertical approach angle into steep / average / flat.

    "Flat" is the *least* negative — a pitch that descends less on its way in.
    Reading these boundaries as ordinary ascending numbers inverts the label.
    """
    if vaa is None:
        return None
    steep_bound, flat_bound = FASTBALL_VAA_TIER_BOUNDS_DEGREES
    if vaa < steep_bound:
        return VAA_TIER_STEEP
    if vaa < flat_bound:
        return VAA_TIER_AVERAGE
    return VAA_TIER_FLAT


def extension_tier(release_extension: float | None) -> str | None:
    """Bucket a release extension into short / average / long."""
    if release_extension is None:
        return None
    short_bound, long_bound = EXTENSION_TIER_BOUNDS_FEET
    if release_extension < short_bound:
        return EXTENSION_TIER_SHORT
    if release_extension <= long_bound:
        return EXTENSION_TIER_AVERAGE
    return EXTENSION_TIER_LONG


def _hit_rate(terminal_pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """Hits per plate appearance over already-reduced terminal pitches."""
    if not terminal_pitches:
        return None
    hits = sum(1 for p in terminal_pitches if p.get("events") in HIT_EVENTS)
    return rate_or_none(hits, len(terminal_pitches))


def _mean_xba(pitches: Sequence[dict[str, Any]]) -> Rate | None:
    """Mean expected batting average over the batted balls among *pitches*.

    The denominator is batted balls, not pitches and not plate appearances: an
    xBA estimate only exists where the ball was put in play. Any other
    denominator would make the sample size a lie about what was measured — the
    same separation Categories 1 and 2 keep for the same reason.
    """
    readings = [
        float(p["estimated_ba_using_speedangle"])
        for p in pitches
        if p.get("estimated_ba_using_speedangle") is not None
    ]
    return rate_or_none(sum(readings), len(readings))


def _usage_share(pitcher_pitches: Sequence[dict[str, Any]], predicate) -> Rate | None:
    """Share of the starter's classified pitches satisfying *predicate*.

    The denominator is pitches carrying a `pitch_type`, **not** the total across
    the three classes. The three shares therefore sum to slightly under one, and
    the unclassified residue — pitchouts, intentional balls, the odd eephus,
    about 0.1 percent of league pitches — stays visible instead of being
    silently redistributed into the classes that happen to be measured.
    """
    typed = [p for p in pitcher_pitches if p.get("pitch_type") is not None]
    if not typed:
        return None
    return rate_or_none(sum(1 for p in typed if predicate(p)), len(typed))


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _starter_fastball_velocity(
    pitcher_pitches: Sequence[dict[str, Any]],
) -> float | None:
    """Mean release speed of the starter's fastballs.

    Fastballs only. A mean over the whole arsenal would grade a power pitcher
    with a deep breaking-ball mix as soft, measuring pitch selection rather than
    arm strength.
    """
    return _mean_or_none(
        [
            float(p["release_speed"])
            for p in pitcher_pitches
            if pitch_class(p.get("pitch_type")) == PITCH_CLASS_FASTBALL
            and p.get("release_speed") is not None
        ]
    )


def _starter_fastball_vaa(pitcher_pitches: Sequence[dict[str, Any]]) -> float | None:
    """Mean vertical approach angle of the starter's fastballs, in degrees."""
    angles = []
    for pitch in pitcher_pitches:
        if pitch_class(pitch.get("pitch_type")) != PITCH_CLASS_FASTBALL:
            continue
        angle = vertical_approach_angle(pitch)
        if angle is not None:
            angles.append(angle)
    return _mean_or_none(angles)


def _starter_extension(pitcher_pitches: Sequence[dict[str, Any]]) -> float | None:
    """Mean release extension across the starter's whole arsenal, in feet.

    Not fastball-restricted, unlike velocity and approach angle: extension is a
    property of the delivery, and a pitcher who released his breaking ball from
    a materially different point would be tipping it.
    """
    return _mean_or_none(
        [
            float(p["release_extension"])
            for p in pitcher_pitches
            if p.get("release_extension") is not None
        ]
    )


# ---------------------------------------------------------------------------
# CALC_16 / CALC_17 / CALC_18 — pitch-class xBA matched to arsenal usage
# ---------------------------------------------------------------------------
#
# Each emits two keys rather than their product. The ROADMAP writes these as
# "Hitter xBA vs pitch type x Pitcher usage %", but the product is not a
# quantity anything can read: a .300 xBA against a starter who throws 55 percent
# fastballs multiplies to .165, which is neither an expected average nor a
# usage. Both terms survive instead, following the `CALC_10` / `CALC_10_XBA`
# precedent, and the composite model weights them when it knows how — that is
# `CALC_75`'s job and it is what issue #34 exists to decide.


def _class_xba_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
    pitch_group: str,
    key: str,
) -> dict[str, Any]:
    """Hitter xBA against one pitch class, plus the starter's usage of it."""
    seen = [
        p for p in hitter_pitches if pitch_class(p.get("pitch_type")) == pitch_group
    ]
    xba = _mean_xba(seen)
    usage = _usage_share(
        pitcher_pitches, lambda p: pitch_class(p.get("pitch_type")) == pitch_group
    )
    return {
        key: PROBABILITY(xba) if xba is not None else None,
        f"{key}_USAGE": MULTIPLIER(usage) if usage is not None else None,
    }


def calc_16_primary_fastball_xba_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_16 — hitter xBA on fastballs, and the starter's fastball usage.

    Fastball means four-seam, sinker, or cutter; see `FASTBALL_TYPES` for why
    the cutter is filed here.
    """
    return _class_xba_match(
        hitter_pitches, pitcher_pitches, PITCH_CLASS_FASTBALL, "CALC_16"
    )


def calc_17_breaking_ball_xba_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_17 — hitter xBA on breaking balls, and the starter's breaking usage."""
    return _class_xba_match(
        hitter_pitches, pitcher_pitches, PITCH_CLASS_BREAKING, "CALC_17"
    )


def calc_18_offspeed_xba_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_18 — hitter xBA on offspeed, and the starter's offspeed usage."""
    return _class_xba_match(
        hitter_pitches, pitcher_pitches, PITCH_CLASS_OFFSPEED, "CALC_18"
    )


# ---------------------------------------------------------------------------
# CALC_19 — run value per 100 pitches on the starter's top-2 pitch types
# ---------------------------------------------------------------------------


def top_pitch_types(pitcher_pitches: Sequence[dict[str, Any]], n: int = 2) -> list[str]:
    """The starter's *n* most frequently thrown pitch types, most-thrown first.

    Ties break alphabetically by type code. Without a deterministic tiebreak two
    equally-thrown pitches would order by dict insertion, which is input order,
    which makes the calculator's output depend on the order Statcast happened to
    return rows in.

    Specific `pitch_type` codes, not the three classes — the ROADMAP asks for
    the pitcher's top pitches, and a starter's two most-thrown offerings are
    routinely both fastballs.
    """
    counts = Counter(
        p["pitch_type"] for p in pitcher_pitches if p.get("pitch_type") is not None
    )
    return [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def calc_19_run_value_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
    top_n: int = 2,
) -> DELTA | None:
    """CALC_19 — hitter run value per 100 pitches on the starter's top-2 types.

    Units are **runs per 100 pitches**, not a rate in [0, 1]. Role is `DELTA`
    for exactly that reason: it is signed and unbounded, and a consumer that
    read it as a probability would be reading a run total. There is no
    probability re-expression of run value, so unlike the tier calculators this
    one is not bucketed into a hit rate.

    Positive favors the hitter. `delta_run_exp` is the change in run expectancy
    from the *batting team's* perspective — verified against the league sample,
    where home runs averaged +1.633 and strikeouts -0.229. The pitcher's mirror
    of the same quantity is `delta_pitcher_run_exp`, which is its negation; a
    calculator that reached for that column by mistake would invert every sign
    and still return plausible-looking numbers.

    The denominator is pitches seen, matching the per-100-pitches unit.
    """
    types = set(top_pitch_types(pitcher_pitches, top_n))
    if not types:
        return None
    values = [
        float(p["delta_run_exp"])
        for p in hitter_pitches
        if p.get("pitch_type") in types and p.get("delta_run_exp") is not None
    ]
    if not values:
        return None
    return DELTA(Rate(100.0 * sum(values) / len(values), len(values)))


# ---------------------------------------------------------------------------
# CALC_20 / CALC_21 / CALC_22 / CALC_23 — tier matches
# ---------------------------------------------------------------------------
#
# All four share one shape, the one `CALC_14` established: bucket some property
# of today's starter, then report the hitter's H/PA over the plate appearances
# that ended on a pitch in the same bucket. The output is a `PROBABILITY` on the
# same scale as every other hit rate in the model, so the composite consumes it
# without needing a response curve nobody has specified.
#
# All four are **season-scoped**, like `CALC_05`-`CALC_08` and `CALC_14`: the
# source pulls one season of pitches per call. The breadth is every pitcher of
# that tier the hitter faced this year, not his career against the tier.


def _tier_hit_rate(
    hitter_pitches: Sequence[dict[str, Any]], tier_of, tier: str
) -> Rate | None:
    """Hitter H/PA over plate appearances that ended on a pitch in *tier*."""
    terminal = terminal_pitch_by_pa(hitter_pitches)
    return _hit_rate([p for p in terminal if tier_of(p) == tier])


def calc_20_velocity_tier_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> PROBABILITY | None:
    """CALC_20 — hitter H/PA in the velocity bracket the starter's fastball sits in.

    The starter is graded on his fastball velocity; the hitter's rate is taken
    over every pitch in that bracket regardless of type, because the bracket is
    defined on speed alone. A hitter who cannot catch up to 97 has that problem
    whether the 97 is a four-seamer or a sinker.
    """
    tier = velocity_tier(_starter_fastball_velocity(pitcher_pitches))
    if tier is None:
        return None
    rate = _tier_hit_rate(
        hitter_pitches, lambda p: velocity_tier(p.get("release_speed")), tier
    )
    return PROBABILITY(rate) if rate is not None else None


def calc_21_vaa_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_21 — hitter H/PA against the starter's fastball approach-angle tier.

    Both sides are restricted to fastballs: pooled across pitch types, vertical
    approach angle mostly measures arsenal mix rather than delivery (see
    `FASTBALL_VAA_TIER_BOUNDS_DEGREES`).

    The ROADMAP frames this as swing plane versus approach angle, and Statcast's
    bat-tracking `attack_angle` gives the swing plane directly. It is emitted as
    the secondary `CALC_21_PLANE_MISMATCH` rather than as this calculator's
    value, because it cannot carry one: the column is entirely null before 2024
    and populated only on pitches the batter swung at. A calculator whose
    primary value depended on it would return None across most of any backtest,
    which is precisely what makes it useless to the validation harness (#35).

    `CALC_21_PLANE_MISMATCH` is signed **degrees**, not a rate, and its
    denominator counts swings. Zero means the swing plane is parallel to the
    incoming pitch — an `attack_angle` of +5 exactly matches a VAA of -5 —
    positive means the swing is steeper upward than the pitch descends, and
    negative means flatter. Measured league mean was +3.4 degrees with a
    standard deviation of 10.7, so the sign alone carries little; magnitude is
    the signal.
    """
    tier = fastball_vaa_tier(_starter_fastball_vaa(pitcher_pitches))
    if tier is None:
        rate = None
    else:
        rate = _tier_hit_rate(
            [
                p
                for p in hitter_pitches
                if pitch_class(p.get("pitch_type")) == PITCH_CLASS_FASTBALL
            ],
            lambda p: fastball_vaa_tier(vertical_approach_angle(p)),
            tier,
        )

    mismatches = []
    for pitch in hitter_pitches:
        attack_angle = pitch.get("attack_angle")
        if attack_angle is None:
            continue
        angle = vertical_approach_angle(pitch)
        if angle is None:
            continue
        mismatches.append(float(attack_angle) + angle)

    return {
        "CALC_21": PROBABILITY(rate) if rate is not None else None,
        "CALC_21_PLANE_MISMATCH": (
            DELTA(Rate(sum(mismatches) / len(mismatches), len(mismatches)))
            if mismatches
            else None
        ),
    }


def calc_22_horizontal_break_acuity(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_22 — hitter H/PA against extreme horizontal break, and how much the
    starter throws.

    "Extreme" is |`pfx_x`| at or above `EXTREME_PFX_X_FEET`. Two keys for the
    same reason `CALC_16` has two: the hitter's vulnerability and the starter's
    ability to exploit it are separate facts, and a hitter who cannot handle a
    sweeper is untroubled by a starter who does not own one.

    `CALC_22_USAGE` is the share of the starter's pitches that break that hard,
    over a denominator of pitches carrying a `pfx_x` reading — every pitch, in
    practice, unlike the classification-dependent usages of `CALC_16`-`CALC_18`.
    """

    def is_extreme(pitch: dict[str, Any]) -> bool:
        pfx_x = pitch.get("pfx_x")
        return pfx_x is not None and abs(float(pfx_x)) >= EXTREME_PFX_X_FEET

    terminal = terminal_pitch_by_pa(hitter_pitches)
    rate = _hit_rate([p for p in terminal if is_extreme(p)])

    with_break = [p for p in pitcher_pitches if p.get("pfx_x") is not None]
    usage = (
        rate_or_none(sum(1 for p in with_break if is_extreme(p)), len(with_break))
        if with_break
        else None
    )

    return {
        "CALC_22": PROBABILITY(rate) if rate is not None else None,
        "CALC_22_USAGE": MULTIPLIER(usage) if usage is not None else None,
    }


def calc_23_extension_match(
    hitter_pitches: Sequence[dict[str, Any]],
    pitcher_pitches: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """CALC_23 — hitter H/PA against the starter's release-extension tier, and
    the perceived-velocity gain that extension buys him.

    Extension is the tier property, not perceived velocity, and the measured
    sample is why. `effective_speed - release_speed` — Statcast's own
    perceived-velocity adjustment — averaged +0.19 mph with a standard deviation
    of 0.80 across 20,545 pitches. That is far too small to move a pitch across
    the ROADMAP's four-mph velocity brackets, so a perceived-velocity tier would
    be `CALC_20` again under a different name. Extension itself has real spread,
    and it is the quantity the ROADMAP actually names.

    `CALC_23_VELO_GAIN` carries the mph the starter's extension is worth,
    averaged over his pitches. Signed `DELTA` in mph: positive means the ball
    arrives sooner than its radar speed implies, negative means a short release
    gives the hitter time back. Its denominator counts pitches with both
    readings.
    """
    tier = extension_tier(_starter_extension(pitcher_pitches))
    if tier is None:
        rate = None
    else:
        rate = _tier_hit_rate(
            hitter_pitches, lambda p: extension_tier(p.get("release_extension")), tier
        )

    gains = [
        float(p["effective_speed"]) - float(p["release_speed"])
        for p in pitcher_pitches
        if p.get("effective_speed") is not None and p.get("release_speed") is not None
    ]

    return {
        "CALC_23": PROBABILITY(rate) if rate is not None else None,
        "CALC_23_VELO_GAIN": (
            DELTA(Rate(sum(gains) / len(gains), len(gains))) if gains else None
        ),
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_03(
    hitter_pitches: Sequence[dict[str, Any]] | None = None,
    pitcher_pitches: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run every Category 3 calculator.

    Absent inputs resolve every key to ``None`` rather than omitting it,
    matching the contract of `compute_category_01` and `compute_category_02` so
    a consumer can rely on the key set without knowing what was fetched.

    Both arguments are pitch-record sequences from
    `calculators.sources.category_03_pitch_arsenal` — `hitter_pitches` is every
    pitch the batter saw this season, `pitcher_pitches` every pitch the starter
    threw.
    """
    hitter_pitches = hitter_pitches or []
    pitcher_pitches = pitcher_pitches or []

    results: dict[str, Any] = {
        "CALC_19": calc_19_run_value_match(hitter_pitches, pitcher_pitches),
        "CALC_20": calc_20_velocity_tier_match(hitter_pitches, pitcher_pitches),
    }
    for calculator in (
        calc_16_primary_fastball_xba_match,
        calc_17_breaking_ball_xba_match,
        calc_18_offspeed_xba_match,
        calc_21_vaa_match,
        calc_22_horizontal_break_acuity,
        calc_23_extension_match,
    ):
        results.update(calculator(hitter_pitches, pitcher_pitches))
    return results
