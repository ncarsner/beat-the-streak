"""Category 5: Ballpark & Environmental Conditions (`CALC_31`-`CALC_40`).

Pure functions over already-normalized records; the network half lives in
`calculators.sources.category_05_ballpark_environment`. See `ROADMAP.md` for the
source definition of each `CALC_NN`.

**The first game-level category.** Categories 1 through 4 ask about a
batter-pitcher pair and Categories 8 and 9 about one player's form. Most of
Category 5 asks about neither: a park factor, an air density, and a wind reading
are properties of the game, identical for all eighteen hitters in it. Only
`CALC_33`, `CALC_37`, `CALC_38`, and `CALC_40` are player-conditioned. That is
why the game-level calculators take an environment record rather than a player
id, and why several of them return a `Rate` whose denominator is not a sample
size (see below).

**Four calculators carry a denominator that is not evidence weight.** `CALC_34`,
`CALC_35`, and `CALC_39` are scalars read off one game or one venue, and
`CALC_36`'s wind speed is likewise a single reading. Their `Rate.denominator` is
set to 1 purely to satisfy the shape, exactly as `CALC_64`'s rest days are.
Issue #34's shrinkage rule must not read those as sample sizes. `CALC_33` and
`CALC_36` do carry a real count, the number of tracked air balls behind the
hitter's spray distribution, because that distribution is genuinely estimated
from a sample.

**`CALC_31` and `CALC_32` are the same quantity at two granularities, and
`CALC_35` contains `CALC_34`.** A handedness-specific park factor already
includes the all-batters effect, and the density-altitude index is computed from
the temperature `CALC_34` reports. Blending either pair applies the same
adjustment twice. Both overlaps are the composite model's to resolve (#39), and
they are stated here for the same reason `CALC_52` states its relationship to the
binomial.

**No invented league constants.** The ROADMAP asks `CALC_34` for a "temperature
modifier" and `CALC_35` for an "index", but the mapping from air density to hit
probability is a league-wide measurement this repo cannot currently make (#38).
So these report physical quantities in documented units and issue #39 owns the
mapping into `p_hit`, following the `CALC_62` precedent of reporting a velocity
delta in raw mph rather than guessing at its run value.

**Nothing here is wired into a run.** The calculators are built and tested in
isolation and get consumed compositely in a later release; the ranked table is
untouched and no Category 5 request is made during a daily run.
"""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

from calculators.common import (
    DELTA,
    HIT_EVENTS,
    IN_PLAY,
    MULTIPLIER,
    PROBABILITY,
    Rate,
    is_competitive,
    rate_or_none,
    terminal_pitch_by_pa,
)

# ---------------------------------------------------------------------------
# Spray geometry
# ---------------------------------------------------------------------------
#
# Statcast publishes a batted ball's landing point as `hc_x` / `hc_y` in its own
# pixel-like coordinate space rather than publishing a spray angle, so the angle
# is solved here. Home plate sits near (125.42, 203.84) and `hc_y` *decreases*
# toward the outfield, which is why the y term is subtracted in that order.
#
# **The sign was validated in both directions before anything depended on it**,
# because a flipped sign turns every pull into an oppo and leaves the output
# entirely plausible -- the same failure class as `delta_run_exp`'s perspective
# in Category 3. Negative is left field, positive is right field. Measured
# 2026-08-09: a right-handed hitter (Judge, 138 tracked air and ground balls)
# distributed LF 62 / CF 42 / RF 34, and an extreme left-handed pull hitter
# (Schwarber, 228) distributed LF 34 / CF 62 / RF 132. Both pull toward their own
# side under this convention, which they must.
HOME_PLATE_HC = (125.42, 203.84)

# Fair territory spans 90 degrees. Readings outside it are dropped rather than
# clamped: they are concentrated in popups and infield dribblers, where the
# landing point is a few feet from the plate and a small coordinate error swings
# the angle wildly. 5 of 143 batted balls for the right-handed probe hitter and
# 15 of 243 for the left-handed one, and of those 20, 15 were popups or ground
# balls.
FAIR_TERRITORY_DEGREES = 45.0

# The five field sectors, left to right, matching the order of `fences_ft` in
# `calculators/data/ballparks.json`. Each is 18 degrees wide. The bounds are
# half-open at the top so a ball lands in exactly one sector, with the last
# sector closed at 45 to keep a ball straight down the right-field line.
SECTOR_NAMES = ("left_line", "left_center", "center", "right_center", "right_line")
SECTOR_BOUNDS = ((-45.0, -27.0), (-27.0, -9.0), (-9.0, 9.0), (9.0, 27.0), (27.0, 45.0))

# Center angle of each sector, used to project a wind vector onto a hitter's
# spray distribution.
SECTOR_CENTERS = tuple((low + high) / 2 for low, high in SECTOR_BOUNDS)

# Batted-ball types that can reach an outfield fence. Ground balls are excluded
# from the geometry and wind calculators: fence distance and wind do not act on a
# ball that never leaves the infield dirt, and including them would dilute the
# spray distribution with the sector a hitter happens to roll the ball into.
AIR_BALL_TYPES = frozenset({"fly_ball", "line_drive", "popup"})


# ---------------------------------------------------------------------------
# Temperature
# ---------------------------------------------------------------------------
#
# The ROADMAP's four buckets, as upper bounds in degrees Fahrenheit. Read as
# "below 60 suppresses, 60 to 75 neutral, 75 to 90 helps, above 90 helps more".
TEMPERATURE_TIER_BOUNDS_F = (60.0, 75.0, 90.0)

# Signed ordinal per tier, in the ROADMAP's own direction (down, neutral, up,
# high up). An ordinal rather than a label so the value fits the `Rate` shape
# every other calculator returns, and so a consumer can order the tiers without
# knowing their names.
TEMPERATURE_TIER_ORDINALS = (-1.0, 0.0, 1.0, 2.0)

# Midpoint of the neutral band, the reference `CALC_34` reports degrees against.
NEUTRAL_TEMPERATURE_F = 67.5


# ---------------------------------------------------------------------------
# Atmosphere
# ---------------------------------------------------------------------------
#
# Constants for the dry-air density at a venue's elevation, via the standard
# barometric formula and the ideal gas law. Nothing here is a baseball
# measurement; it is physics, which is the point. `CALC_35` needs no league
# sample and is therefore unaffected by issue #38.
SEA_LEVEL_PRESSURE_PA = 101325.0
SEA_LEVEL_TEMPERATURE_K = 288.15
DRY_AIR_GAS_CONSTANT = 287.058  # J/(kg K)
BAROMETRIC_LAPSE_COEFFICIENT = 2.25577e-5
BAROMETRIC_EXPONENT = 5.25588
FEET_PER_METRE = 3.28084

# Density of dry air at the ISA sea-level reference, the denominator of the
# index. Roughly 1.225 kg/m^3.
STANDARD_AIR_DENSITY = SEA_LEVEL_PRESSURE_PA / (
    DRY_AIR_GAS_CONSTANT * SEA_LEVEL_TEMPERATURE_K
)


# ---------------------------------------------------------------------------
# Weather and wind free text
# ---------------------------------------------------------------------------
#
# The boxscore reports both as prose: "89 degrees, Partly Cloudy." and "11 mph,
# Out To RF.". Every vocabulary below is enumerated **in both directions** rather
# than as one set plus a complement, for the reason `OUT_EVENTS` and
# `ON_BASE_EVENTS` are: an unrecognized value must fall out of the calculation
# entirely, not be scored as the other thing. A wind direction this module does
# not know returns None, never a neutral zero.
#
# Measured across 78 games on five dates spanning April to August 2026:
# conditions were Partly Cloudy (21), Clear (18), Roof Closed (12), Cloudy (9),
# Overcast (6), Dome (2), Sunny (2); wind directions were None (15), R To L (12),
# Out To CF (10), L To R (9), Out To LF (7), In From CF (5), Out To RF (5),
# In From LF (4), In From RF (3).

_TEMPERATURE_PATTERN = re.compile(r"(-?\d+)\s*degrees", re.IGNORECASE)
_WIND_SPEED_PATTERN = re.compile(r"(\d+)\s*mph", re.IGNORECASE)

# Condition strings that mean the game was played under a shut roof. Both are
# real and distinct: a retractable park reports "Roof Closed", a fixed-roof park
# reports "Dome".
CLOSED_ROOF_CONDITIONS = frozenset({"roof closed", "dome"})

# Condition strings that mean the game was played in open air. Enumerated so an
# unrecognized condition yields an unknown roof state rather than defaulting to
# open, which would silently tell `CALC_39` that a domed game was outdoors.
OPEN_AIR_CONDITIONS = frozenset(
    {"clear", "sunny", "partly cloudy", "cloudy", "overcast", "rain", "drizzle"}
)

# Wind directions that carry an in/out component, mapped to the sector the wind
# blows along and the sign of that component. "Out To LF" pushes a ball hit to
# left field farther; "In From LF" holds it up.
WIND_VECTORS = {
    "out to lf": ("left_line", 1.0),
    "out to cf": ("center", 1.0),
    "out to rf": ("right_line", 1.0),
    "in from lf": ("left_line", -1.0),
    "in from cf": ("center", -1.0),
    "in from rf": ("right_line", -1.0),
}

# Directions with no in/out component at all. These resolve to an assisting wind
# of exactly 0.0 mph, which is a measurement and not a missing value: a crossfield
# or calm wind genuinely does not help or hurt carry.
NEUTRAL_WIND_DIRECTIONS = frozenset({"l to r", "r to l", "none", "calm", "varies"})

# Smallest open-roof share of a retractable park's plate appearances that still
# permits solving for its open-roof index. See `open_roof_index` for the error
# propagation this threshold controls. Measured open shares on 2026-08-09:
# T-Mobile .81, American Family .48, Rogers Centre .46, Chase Field .19,
# loanDepot .13, Globe Life .08, Daikin Park .004.
MIN_OPEN_ROOF_SHARE = 0.15


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _normalize_phrase(text: str) -> str:
    """Lower-case *text* and strip the trailing period the boxscore adds."""
    return text.strip().rstrip(".").strip().lower()


def parse_temperature_f(weather: str | None) -> float | None:
    """Degrees Fahrenheit from a boxscore ``Weather`` string, or None.

    ``"89 degrees, Partly Cloudy."`` yields 89.0. A string with no temperature,
    or no string at all, yields None; the field is absent before first pitch for
    most games (see the source module).
    """
    if not weather:
        return None
    match = _TEMPERATURE_PATTERN.search(weather)
    return float(match.group(1)) if match else None


def parse_condition(weather: str | None) -> str | None:
    """The condition phrase from a boxscore ``Weather`` string, normalized.

    ``"89 degrees, Partly Cloudy."`` yields ``"partly cloudy"``. Returns None
    when the string carries no comma-separated condition clause.
    """
    if not weather or "," not in weather:
        return None
    condition = _normalize_phrase(weather.split(",", 1)[1])
    return condition or None


def parse_wind(wind: str | None) -> tuple[float, str] | None:
    """``(speed_mph, direction)`` from a boxscore ``Wind`` string, or None.

    ``"11 mph, Out To RF."`` yields ``(11.0, "out to rf")``. Returns None when
    either half is missing, so a caller cannot mistake an unparsed reading for a
    calm one.
    """
    if not wind or "," not in wind:
        return None
    speed_match = _WIND_SPEED_PATTERN.search(wind)
    direction = _normalize_phrase(wind.split(",", 1)[1])
    if speed_match is None or not direction:
        return None
    return float(speed_match.group(1)), direction


def spray_angle(batted_ball: dict[str, Any]) -> float | None:
    """Spray angle in degrees, negative to left field, or None when untracked.

    Solved from `hc_x` / `hc_y` because Statcast publishes no angle column.
    Readings outside fair territory are rejected rather than clamped; see
    `FAIR_TERRITORY_DEGREES`.
    """
    hc_x = batted_ball.get("hc_x")
    hc_y = batted_ball.get("hc_y")
    if hc_x is None or hc_y is None:
        return None
    origin_x, origin_y = HOME_PLATE_HC
    try:
        angle = math.degrees(math.atan2(float(hc_x) - origin_x, origin_y - float(hc_y)))
    except (TypeError, ValueError):
        return None
    if not -FAIR_TERRITORY_DEGREES <= angle <= FAIR_TERRITORY_DEGREES:
        return None
    return angle


def sector_for_angle(angle: float) -> str | None:
    """The field sector *angle* falls in, or None when it is out of play."""
    for name, (low, high) in zip(SECTOR_NAMES, SECTOR_BOUNDS):
        if low <= angle < high:
            return name
    if angle == FAIR_TERRITORY_DEGREES:
        return SECTOR_NAMES[-1]
    return None


# ---------------------------------------------------------------------------
# Reductions
# ---------------------------------------------------------------------------


def air_balls(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Competitive plate appearances that ended in a ball hit into the air.

    Reduce to the terminal pitch first, then drop non-competitive games, then
    filter on batted-ball type: the same ordering `plate_appearances` uses in
    Category 8, and for the same reason. Spring training is excluded because
    Statcast's tracked columns behave differently there (see issue #42).
    """
    terminal = [p for p in terminal_pitch_by_pa(pitches) if is_competitive(p)]
    return [
        pa
        for pa in terminal
        if pa.get("description") == IN_PLAY and pa.get("bb_type") in AIR_BALL_TYPES
    ]


def spray_distribution(
    pitches: Sequence[dict[str, Any]],
) -> tuple[dict[str, float], int]:
    """``({sector: share}, tracked_count)`` over the hitter's air balls.

    Shares sum to 1.0 across the sectors the hitter actually reached. An empty
    distribution and a count of 0 mean no tracked air ball, which every consumer
    treats as no sample rather than as a uniform spray.
    """
    counts = dict.fromkeys(SECTOR_NAMES, 0)
    tracked = 0
    for batted_ball in air_balls(pitches):
        angle = spray_angle(batted_ball)
        if angle is None:
            continue
        sector = sector_for_angle(angle)
        if sector is None:
            continue
        counts[sector] += 1
        tracked += 1
    if not tracked:
        return {}, 0
    return {name: count / tracked for name, count in counts.items()}, tracked


def league_mean_fences(ballparks: dict[str, Any]) -> list[float] | None:
    """Mean fence distance per sector across every venue in *ballparks*.

    The reference `CALC_33` measures today's park against. Computed from the
    table rather than stored in it, so the two can never drift apart.
    """
    rows = [
        venue["fences_ft"]
        for venue in ballparks.values()
        if isinstance(venue.get("fences_ft"), list)
        and len(venue["fences_ft"]) == len(SECTOR_NAMES)
    ]
    if not rows:
        return None
    return [sum(values) / len(rows) for values in zip(*rows)]


def _park_index(
    park_factors: dict[str, Any],
    venue_id: int | str,
    grouping: str,
    index_key: str,
) -> tuple[float, int] | None:
    """``(index, n_pa)`` for one venue/grouping/index, or None when absent."""
    venue = park_factors.get(str(venue_id))
    if not venue:
        return None
    row = (venue.get("groupings") or {}).get(grouping)
    if not row:
        return None
    index = row.get(index_key)
    sample = row.get("n_pa")
    if index is None or not sample:
        return None
    return float(index), int(sample)


# ---------------------------------------------------------------------------
# CALC_31 / CALC_32 -- park factors
# ---------------------------------------------------------------------------
#
# Savant publishes park factors as an index where 100 is league-neutral, so a
# park factor of 104 means 4 percent more of that outcome than a neutral park.
# Dividing by 100 puts them on a multiplier scale.
#
# `MULTIPLIER`, not `PROBABILITY`: 1.04 is not a hit rate, and blending it into
# `p_hit` directly would be meaningless. The denominator is Savant's own `n_pa`,
# which is a genuine sample size (52,026 plate appearances at Camden Yards over
# the three-year window) and the only one in this category's game-level half.


def calc_31_ballpark_hit_factor(
    park_factors: dict[str, Any],
    venue_id: int | str,
    index_key: str = "index_hits",
) -> MULTIPLIER | None:
    """CALC_31: the venue's 3-year rolling park factor for base hits.

    *index_key* selects which published index to read. The default is
    `index_hits`, the ROADMAP's "base hits (1B/2B/3B)" aggregate; the component
    `index_1b`, `index_2b`, and `index_3b` are carried in the table for a
    consumer that wants to weight them separately.

    Returns None for a venue the table does not cover. That is a real case rather
    than a defect: Savant's leaderboard carried 29 of the 30 venues on
    2026-08-09, omitting Sutter Health Park, whose three-year window has too
    little history behind it.
    """
    found = _park_index(park_factors, venue_id, "All", index_key)
    if found is None:
        return None
    index, sample = found
    return MULTIPLIER(Rate(index / 100.0, sample))


def calc_32_ballpark_handedness_factor(
    park_factors: dict[str, Any],
    venue_id: int | str,
    bat_side: str | None,
    index_key: str = "index_hits",
) -> MULTIPLIER | None:
    """CALC_32: the venue's park factor for base hits by this batter's hand.

    Strictly more specific than `CALC_31` and therefore **not** to be blended
    alongside it: an L-split park factor already contains the all-batters effect,
    and using both applies the park adjustment twice.

    A switch hitter has no fixed hand, so the caller must resolve which side he
    bats from today and pass that; passing None returns None rather than falling
    back to the all-batters factor, which would silently be `CALC_31` under a
    different key.
    """
    if bat_side not in ("L", "R"):
        return None
    found = _park_index(park_factors, venue_id, bat_side, index_key)
    if found is None:
        return None
    index, sample = found
    return MULTIPLIER(Rate(index / 100.0, sample))


# ---------------------------------------------------------------------------
# CALC_33 -- geometry match
# ---------------------------------------------------------------------------


def calc_33_geometry_match(
    pitches: Sequence[dict[str, Any]],
    ballparks: dict[str, Any],
    venue_id: int | str,
) -> DELTA | None:
    """CALC_33: how much deeper this park is where the hitter hits the ball.

    Value is in **feet**, positive when the park's fences are farther than the
    league mean across the sectors this hitter actually uses, negative when they
    are closer. A pull-heavy left-handed hitter at a park with a short right
    field gets a negative reading; the same hitter at Comerica gets a positive
    one.

    **The sign of this quantity's effect on hit probability is not settled, and
    this calculator deliberately does not decide it.** A shorter fence turns fly
    balls into home runs, which are hits, while a deeper outfield leaves more
    room for a ball to fall in, which are also hits. Which dominates is a
    league-wide measurement this repo cannot currently make (#38), so `CALC_33`
    reports the physical differential and issue #39 owns the mapping, exactly as
    `CALC_62` reports a velocity delta in mph.

    **Distance only: the API publishes no wall heights.** Fenway's left field
    reads here as a 310-foot line with nothing to say the wall above it is 37
    feet tall, which is precisely the case where distance alone misleads. A
    height source would change this calculator's answer at four or five parks.

    The denominator is the number of tracked air balls behind the spray
    distribution, which is a real sample size, unlike most of this category.
    """
    shares, tracked = spray_distribution(pitches)
    if not tracked:
        return None

    venue = ballparks.get(str(venue_id))
    fences = (venue or {}).get("fences_ft")
    if not isinstance(fences, list) or len(fences) != len(SECTOR_NAMES):
        return None

    league = league_mean_fences(ballparks)
    if league is None:
        return None

    differential = sum(
        shares.get(name, 0.0) * (float(fence) - mean)
        for name, fence, mean in zip(SECTOR_NAMES, fences, league)
    )
    return DELTA(Rate(differential, tracked))


# ---------------------------------------------------------------------------
# CALC_34 -- temperature
# ---------------------------------------------------------------------------


def temperature_tier(temperature_f: float) -> float:
    """The ROADMAP's temperature tier as a signed ordinal.

    -1 below 60F, 0 from 60 to 75, +1 from 75 to 90, +2 above 90, matching the
    ROADMAP's "down / neutral / up / high up". Boundaries are inclusive at the
    bottom of each band.
    """
    for bound, ordinal in zip(TEMPERATURE_TIER_BOUNDS_F, TEMPERATURE_TIER_ORDINALS):
        if temperature_f < bound:
            return ordinal
    return TEMPERATURE_TIER_ORDINALS[-1]


def calc_34_temperature(weather: str | None) -> dict[str, Any]:
    """CALC_34: game temperature, as degrees off neutral and as a tier.

    `CALC_34` is degrees Fahrenheit above the neutral band's midpoint, signed.
    `CALC_34_TIER` is the ROADMAP's four-bucket ordinal. Both are `DELTA`: a
    temperature is not a probability and not a multiplier, and the magnitude of
    its effect on hit rate is a league constant this repo cannot measure (#38).

    **Neither denominator is a sample size.** Both are 1, set only to satisfy the
    `Rate` shape, the same as `CALC_64`'s rest days. Issue #34's shrinkage rule
    must not read them as evidence weight.

    `CALC_35` is computed from this same temperature, so the two are not
    independent and blending both double-counts the thermal effect.
    """
    temperature = parse_temperature_f(weather)
    if temperature is None:
        return {"CALC_34": None, "CALC_34_TIER": None}
    return {
        "CALC_34": DELTA(Rate(temperature - NEUTRAL_TEMPERATURE_F, 1)),
        "CALC_34_TIER": DELTA(Rate(temperature_tier(temperature), 1)),
    }


# ---------------------------------------------------------------------------
# CALC_35 -- air density
# ---------------------------------------------------------------------------


def air_density(elevation_ft: float, temperature_f: float) -> float:
    """Dry-air density in kg/m^3 at *elevation_ft* and *temperature_f*.

    Station pressure comes from the standard barometric formula and density from
    the ideal gas law. This is physics rather than a baseball measurement, which
    is what makes `CALC_35` immune to issue #38's missing league sample.
    """
    metres = elevation_ft / FEET_PER_METRE
    pressure = (
        SEA_LEVEL_PRESSURE_PA
        * (1.0 - BAROMETRIC_LAPSE_COEFFICIENT * metres) ** BAROMETRIC_EXPONENT
    )
    kelvin = (temperature_f - 32.0) * 5.0 / 9.0 + 273.15
    return pressure / (DRY_AIR_GAS_CONSTANT * kelvin)


def calc_35_air_density_index(
    weather: str | None,
    ballparks: dict[str, Any],
    venue_id: int | str,
) -> MULTIPLIER | None:
    """CALC_35: density-altitude index, standard sea-level density over actual.

    Above 1.0 means thinner air than the sea-level standard, so a batted ball
    carries farther and a pitch breaks less. Coors Field on a warm evening lands
    near 1.20; a cold night at sea level lands slightly below 1.0.

    **Humidity is not available and is not guessed at.** The boxscore reports a
    condition phrase ("Partly Cloudy") and no relative humidity, so this is a
    dry-air computation. Water vapour is lighter than dry air, so humid
    conditions are marginally *less* dense than computed here and the index is a
    slight underestimate on muggy days. The error is under half a percent across
    the realistic range, which is small next to the 20 percent Coors effect the
    calculator exists to capture.

    `MULTIPLIER` because it is a ratio, not a rate. Its denominator is 1 and is
    not a sample size. It contains `CALC_34`'s temperature, so the two must not
    both be blended.
    """
    temperature = parse_temperature_f(weather)
    if temperature is None:
        return None
    venue = ballparks.get(str(venue_id))
    elevation = (venue or {}).get("elevation_ft")
    if elevation is None:
        return None
    density = air_density(float(elevation), temperature)
    if density <= 0:
        return None
    return MULTIPLIER(Rate(STANDARD_AIR_DENSITY / density, 1))


# ---------------------------------------------------------------------------
# CALC_36 -- wind
# ---------------------------------------------------------------------------


def calc_36_wind_effect(
    wind: str | None,
    pitches: Sequence[dict[str, Any]],
) -> DELTA | None:
    """CALC_36: assisting wind in mph, projected onto the hitter's spray pattern.

    Positive means the wind pushes the ball out where this hitter hits it;
    negative means it holds the ball up there. A 12 mph wind out to right field
    helps a left-handed pull hitter far more than a right-handed one, and this is
    the calculator that says by how much.

    **The boxscore's wind direction is already field-relative** ("Out To RF",
    "In From CF", "L To R"), so the venue's compass orientation is not needed and
    is not used. The projection is a cosine of the angular separation between the
    wind's sector and each of the hitter's, so a wind out to right field still
    contributes something to a ball hit to right-center.

    A crossfield or calm wind returns **0.0 mph**, which is a measurement rather
    than a missing value: such a wind genuinely neither helps nor hurts carry.
    An unrecognized direction returns None instead, so a vocabulary gap can never
    be mistaken for a calm day.

    **The spread between two hitters is narrower than it looks like it should
    be, and that is physics rather than a weak projection.** Fair territory spans
    only 90 degrees, so the widest possible separation between a wind's sector
    and a batted ball's is 72 degrees and the cosine never falls below 0.31: a
    wind blowing out to right field still pushes a ball hit to left field
    outward, just less. Measured on a 12 mph wind out to right field, an extreme
    left-handed pull hitter gained 9.90 mph of assist against a right-handed
    hitter's 9.01. Real, in the right direction, and small.

    The denominator is the tracked air-ball count behind the spray distribution.
    The wind reading itself is a single scalar, but what makes this value
    hitter-specific is the distribution, and that is genuinely estimated.
    """
    parsed = parse_wind(wind)
    if parsed is None:
        return None
    speed, direction = parsed

    shares, tracked = spray_distribution(pitches)
    if not tracked:
        return None

    if direction in NEUTRAL_WIND_DIRECTIONS:
        return DELTA(Rate(0.0, tracked))

    vector = WIND_VECTORS.get(direction)
    if vector is None:
        return None
    wind_sector, sign = vector
    wind_angle = SECTOR_CENTERS[SECTOR_NAMES.index(wind_sector)]

    assist = 0.0
    for name, centre in zip(SECTOR_NAMES, SECTOR_CENTERS):
        separation = math.radians(centre - wind_angle)
        assist += shares.get(name, 0.0) * math.cos(separation)
    return DELTA(Rate(speed * sign * assist, tracked))


# ---------------------------------------------------------------------------
# CALC_37 / CALC_38 -- situational splits
# ---------------------------------------------------------------------------
#
# Both read the MLB Stats API's `statSplits` lines, which arrive as a mapping of
# situation code to a counting-stat line. Both sides of the matchup are reported,
# per the ROADMAP's "Hitter & Pitcher splits" and "Hitter Home vs Road BA &
# Pitcher Home vs Road BA allowed".
#
# **The denominator is plate appearances, never at-bats.** The API returns `avg`
# alongside, and taking it would put an at-bat-denominated rate into a model
# whose `p_hit` is per plate appearance -- the same units error `CALC_57` exists
# to warn about. The two differ by roughly 10 percent, enough to look right.
#
# The two sides name that denominator differently in the raw response: a hitting
# split carries `plateAppearances`, a pitching split carries `battersFaced` and
# leaves `plateAppearances` **null** (verified on both groups, 2026-08-09).
# `fetch_stat_splits` already normalizes the pitching case at the source
# boundary, which is where Category 2 put it, so both sides arrive here under the
# same key and this module never sees `battersFaced`.

PA_FIELD = "plateAppearances"


def _split_hit_rate(splits: dict[str, Any] | None, code: str) -> Rate | None:
    """Hits per plate appearance for one situation code."""
    line = (splits or {}).get(code)
    if not line:
        return None
    return rate_or_none(line.get("hits") or 0, line.get(PA_FIELD) or 0)


def _situational(
    batter_splits: dict[str, Any] | None,
    pitcher_splits: dict[str, Any] | None,
    code: str,
    keys: tuple[str, str],
) -> dict[str, Any]:
    batter_key, pitcher_key = keys
    batter = _split_hit_rate(batter_splits, code)
    pitcher = _split_hit_rate(pitcher_splits, code)
    return {
        batter_key: PROBABILITY(batter) if batter is not None else None,
        pitcher_key: PROBABILITY(pitcher) if pitcher is not None else None,
    }


def calc_37_day_night(
    batter_splits: dict[str, Any] | None,
    pitcher_splits: dict[str, Any] | None,
    day_night: str | None,
) -> dict[str, Any]:
    """CALC_37: hitter and pitcher hit rates in today's day-or-night condition.

    *day_night* is the schedule record's own ``dayNight`` value, "day" or
    "night". Anything else returns both keys as None rather than defaulting to
    one of them.

    Genuine `PROBABILITY` on both sides: these are hits per plate appearance,
    the model's own scale.
    """
    code = {"day": "d", "night": "n"}.get((day_night or "").strip().lower())
    if code is None:
        return {"CALC_37_BATTER": None, "CALC_37_PITCHER": None}
    return _situational(
        batter_splits, pitcher_splits, code, ("CALC_37_BATTER", "CALC_37_PITCHER")
    )


def calc_38_home_away(
    batter_splits: dict[str, Any] | None,
    pitcher_splits: dict[str, Any] | None,
    batter_is_home: bool | None,
) -> dict[str, Any]:
    """CALC_38: hitter and pitcher hit rates at home versus on the road.

    *batter_is_home* is from the batter's perspective, and the pitcher's split is
    resolved to the **opposite** side of the same game: when the hitter is home
    the opposing starter is away, so the pitcher's away line is the relevant one.
    Reading both from the same code would compare the hitter's home form against
    a pitcher's home form in a game where only one of them can be home.
    """
    if batter_is_home is None:
        return {"CALC_38_BATTER": None, "CALC_38_PITCHER": None}
    batter_code = "h" if batter_is_home else "a"
    pitcher_code = "a" if batter_is_home else "h"
    batter = _split_hit_rate(batter_splits, batter_code)
    pitcher = _split_hit_rate(pitcher_splits, pitcher_code)
    return {
        "CALC_38_BATTER": PROBABILITY(batter) if batter is not None else None,
        "CALC_38_PITCHER": PROBABILITY(pitcher) if pitcher is not None else None,
    }


# ---------------------------------------------------------------------------
# CALC_39 -- roof status
# ---------------------------------------------------------------------------


def roof_state(weather: str | None, roof_type: str | None) -> str | None:
    """``"closed"``, ``"open"``, ``"none"``, or None when undetermined.

    ``"none"`` means the park has no roof at all, which is a different answer
    from an unknown state. A retractable park whose condition phrase is neither a
    closed-roof phrase nor a recognized open-air one returns None rather than
    guessing, because guessing "open" would quietly file a domed game outdoors.
    """
    if (roof_type or "").strip().lower() == "open":
        return "none"
    condition = parse_condition(weather)
    if condition is None:
        return None
    if condition in CLOSED_ROOF_CONDITIONS:
        return "closed"
    if condition in OPEN_AIR_CONDITIONS:
        return "open"
    return None


def open_roof_index(
    all_index: float,
    all_sample: int,
    closed_index: float,
    closed_sample: int,
) -> float | None:
    """Solve for a park's open-roof index from its all-conditions and closed ones.

    Savant publishes no working roof-open grouping -- the request returns zero
    rows -- so the open-roof index is recovered rather than read. The
    all-conditions index is the plate-appearance-weighted blend of the two
    states, and the closed share is exactly ``closed_sample / all_sample``, which
    makes the algebra determinate::

        A = f*C + (1 - f)*O   ->   O = (A - f*C) / (1 - f)

    **Returns None when the open share is too thin to invert.** The published
    indexes are integers, so `all_index` carries up to half a point of rounding
    error, and that error is divided by ``1 - f`` on the way out: at a 15 percent
    open share it becomes 3.3 index points, and at Daikin Park's measured 0.4
    percent it becomes 135, which is not a number. `MIN_OPEN_ROOF_SHARE` is the
    line, and four of the seven retractable parks fall below it.

    The blend is treated as linear in plate appearances. That holds because the
    index is a rate ratio against a common league baseline, so the numerator
    rates blend by plate appearance and the shared denominator divides out.
    """
    if all_sample <= 0 or closed_sample <= 0:
        return None
    closed_share = closed_sample / all_sample
    open_share = 1.0 - closed_share
    if open_share < MIN_OPEN_ROOF_SHARE:
        return None
    return (all_index - closed_share * closed_index) / open_share


def calc_39_roof_status(
    park_factors: dict[str, Any],
    ballparks: dict[str, Any],
    venue_id: int | str,
    weather: str | None,
    index_key: str = "index_hits",
) -> MULTIPLIER | None:
    """CALC_39: how the park plays under today's roof state, relative to open air.

    Four branches, each defensible on its own terms:

    - **Open-air park.** Returns exactly 1.0. Not an invented neutral: a park
      with no roof has only one condition, so there is no adjustment to make and
      the full `n_pa` sits behind that statement.
    - **Fixed dome**, where the closed sample is the whole sample. Also 1.0, for
      the same reason: every game there was played under the roof, so the park's
      own factor already describes today's conditions. Measured at Tropicana
      Field, whose closed and all-conditions samples are both 31,614 plate
      appearances.
    - **Retractable park, roof closed.** Returns the closed index over the
      *solved* open-roof index (see `open_roof_index`), which is the real
      closed-versus-open effect rather than a ratio against a blend that already
      contains the closed games.
    - **Roof open, an undetermined state, or an open share too thin to invert.**
      Returns None.

    **The effect this measures is small.** Across the seven retractable parks the
    closed-roof hit index sat within two points of the all-conditions index, and
    for three of them the two were equal at the published integer resolution.
    That is a finding rather than a defect: shutting a roof does not change how
    often a ball falls in for a hit by very much, and a calculator that reported
    a large adjustment here would be wrong.
    """
    venue = ballparks.get(str(venue_id))
    state = roof_state(weather, (venue or {}).get("roof_type"))
    if state is None or state == "open":
        return None

    baseline = _park_index(park_factors, venue_id, "All", index_key)
    if baseline is None:
        return None
    all_index, all_sample = baseline

    if state == "none":
        return MULTIPLIER(Rate(1.0, all_sample))

    closed = _park_index(park_factors, venue_id, "roof_closed", index_key)
    if closed is None:
        return None
    closed_index, closed_sample = closed

    if closed_sample >= all_sample:
        # A fixed dome: every plate appearance was played under the roof, so
        # there is no open-air counterfactual and no adjustment to make.
        return MULTIPLIER(Rate(1.0, closed_sample))

    open_index = open_roof_index(all_index, all_sample, closed_index, closed_sample)
    if open_index is None or open_index <= 0:
        return None
    return MULTIPLIER(Rate(closed_index / open_index, closed_sample))


# ---------------------------------------------------------------------------
# CALC_40 -- venue familiarity
# ---------------------------------------------------------------------------


def calc_40_venue_familiarity(
    pitches: Sequence[dict[str, Any]],
    home_team: str | None,
) -> PROBABILITY | None:
    """CALC_40: the hitter's hit rate at today's venue.

    *home_team* is the Statcast club abbreviation of the venue's home club, which
    is how a Statcast row identifies where a game was played -- there is no venue
    column. A hitter's own home park therefore resolves through the same path as
    a visiting one, which is why this is not restricted to road games despite the
    ROADMAP's "current visiting venue" phrasing: the quantity is the same and
    limiting it would discard half the sample for no reason.

    **Season-scoped, which is the honest limit here.** The Statcast fetcher pulls
    one season, so "historical hit rate at this venue" is really "this season's",
    typically 3 to 10 games and a thin sample. The denominator travels with the
    rate so #34's shrinkage can discount it accordingly, and a career reading
    would need a multi-season pull that does not exist yet.
    """
    if not home_team:
        return None
    terminal = [p for p in terminal_pitch_by_pa(pitches) if is_competitive(p)]
    at_venue = [pa for pa in terminal if pa.get("home_team") == home_team]
    if not at_venue:
        return None
    hits = sum(1 for pa in at_venue if pa.get("events") in HIT_EVENTS)
    rate = rate_or_none(hits, len(at_venue))
    return PROBABILITY(rate) if rate is not None else None


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_05(
    environment: dict[str, Any] | None = None,
    ballparks: dict[str, Any] | None = None,
    park_factors: dict[str, Any] | None = None,
    batter_pitches: Sequence[dict[str, Any]] | None = None,
    batter_splits: dict[str, Any] | None = None,
    pitcher_splits: dict[str, Any] | None = None,
    bat_side: str | None = None,
) -> dict[str, Any]:
    """Run every Category 5 calculator.

    Absent input resolves every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_04` and
    `compute_category_08`, so a consumer can rely on the key set without knowing
    what was fetched.

    *environment* is one record from
    `calculators.sources.category_05_ballpark_environment.fetch_game_environment`:
    ``venue_id``, ``weather``, ``wind``, ``day_night``, ``home_team``, and
    ``batter_is_home``. *ballparks* and *park_factors* are the two checked-in
    tables from `calculators.baselines`.
    """
    environment = environment or {}
    ballparks = ballparks or {}
    park_factors = park_factors or {}
    pitches = batter_pitches or []

    venue_id = environment.get("venue_id")
    weather = environment.get("weather")
    home_team = environment.get("home_team")

    results: dict[str, Any] = {
        "CALC_31": None,
        "CALC_32": None,
        "CALC_33": None,
        "CALC_35": None,
        "CALC_36": calc_36_wind_effect(environment.get("wind"), pitches),
        "CALC_39": None,
        "CALC_40": calc_40_venue_familiarity(pitches, home_team),
    }
    if venue_id is not None:
        results["CALC_31"] = calc_31_ballpark_hit_factor(park_factors, venue_id)
        results["CALC_32"] = calc_32_ballpark_handedness_factor(
            park_factors, venue_id, bat_side
        )
        results["CALC_33"] = calc_33_geometry_match(pitches, ballparks, venue_id)
        results["CALC_35"] = calc_35_air_density_index(weather, ballparks, venue_id)
        results["CALC_39"] = calc_39_roof_status(
            park_factors, ballparks, venue_id, weather
        )

    results.update(calc_34_temperature(weather))
    results.update(
        calc_37_day_night(batter_splits, pitcher_splits, environment.get("day_night"))
    )
    results.update(
        calc_38_home_away(
            batter_splits, pitcher_splits, environment.get("batter_is_home")
        )
    )
    return results
