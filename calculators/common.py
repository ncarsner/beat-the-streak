"""Types and helpers shared by every calculator category.

Nothing here is specific to one category, and nothing here touches the network.
"""

import datetime
from dataclasses import dataclass
from typing import Any, Iterable, NamedTuple, Sequence


# Counting stats carried through from a MLB Stats API hitting split. Every
# category that divides one count by another draws its terms from this set.
COUNTING_STATS = ("plateAppearances", "atBats", "hits", "strikeOuts", "baseOnBalls")

# Statcast `events` values that put a hit on the board. Lives here rather than
# in one category because every category that counts hits off pitch-level rows
# needs the same set, and two copies would eventually disagree.
HIT_EVENTS = frozenset({"single", "double", "triple", "home_run"})

# Statcast `events` values that retire the batter, and those on which he reached
# base. Both are enumerated explicitly rather than one set plus a "everything
# else is the other thing" complement rule, and that is the whole point of
# writing them out: `events` also carries values that are not batter outcomes at
# all. A plate appearance can end on `truncated_pa`, `caught_stealing_2b`,
# `pickoff_1b`, `wild_pitch`, or `runner_double_play`, none of which say anything
# about whether the hitter was retired. Under a complement rule every one of them
# would score as an out, which is the same silent-negative failure mode
# documented for CALC_14 in issue #37.
#
# A value in neither set is dropped from numerator *and* denominator, so an
# unrecognized event shrinks the sample instead of biasing the rate. Verified
# against the cached probe frames: 774 of 774 plate appearances classified into
# one of the two sets, zero residue (2026-08-09).
OUT_EVENTS = frozenset(
    {
        "field_out",
        "strikeout",
        "strikeout_double_play",
        "grounded_into_double_play",
        "force_out",
        "double_play",
        "triple_play",
        "sac_fly",
        "sac_bunt",
        "sac_fly_double_play",
        "sac_bunt_double_play",
        "fielders_choice_out",
        "other_out",
        "batter_interference",
    }
)

# `fielders_choice` (batter reached, a runner was retired) is a reach; its
# sibling `fielders_choice_out` (the batter himself was retired) is an out, and
# the two codes differ by one word. `field_error` is a reach for the same reason
# it is not an at-bat hit: the batter is on base.
ON_BASE_EVENTS = HIT_EVENTS | frozenset(
    {
        "walk",
        "intent_walk",
        "hit_by_pitch",
        "catcher_interf",
        "field_error",
        "fielders_choice",
    }
)


class Rate(NamedTuple):
    """A rate paired with the sample size it was computed over.

    `denominator` travels with the rate because most of the model's inputs are
    small samples — a 1-for-2 head-to-head line is not evidence of a .500
    hitter. The composite model (CALC_75) needs the count to shrink the rate
    toward a prior, so no calculator returns a bare float.
    """

    rate: float
    denominator: int


# ---------------------------------------------------------------------------
# Statcast pitch `description` vocabulary
# ---------------------------------------------------------------------------
#
# Promoted here after three categories defined the same sets independently
# (Categories 1, 4, and 8). They agreed on membership at the time of the move,
# which is the good case and not one to rely on twice.

# The `description` value for a pitch the batter put into play.
IN_PLAY = "hit_into_play"

# Swings where the bat missed the ball entirely. `foul_tip` and `foul_bunt` are
# deliberately excluded: a tipped ball is contact even though it is scored a
# strike, which is why a caught foul tip on strike three is a strikeout by rule
# rather than a foul ball. Savant counts it as contact and so does this model.
WHIFF_DESCRIPTIONS = frozenset(
    {
        "swinging_strike",
        "swinging_strike_blocked",
        "missed_bunt",
    }
)

# Contact of any kind, fair or foul, in play or not.
CONTACT_DESCRIPTIONS = frozenset(
    {
        "foul",
        "foul_tip",
        "foul_bunt",
        "bunt_foul_tip",
        IN_PLAY,
    }
)

# Anything the batter offered at. Fouls included: the denominator of a whiff
# rate is "total swings", not "swings that could have been hits".
SWING_DESCRIPTIONS = WHIFF_DESCRIPTIONS | CONTACT_DESCRIPTIONS

# Exit velocity at or above which a batted ball is "hard hit", per Statcast.
HARD_HIT_MPH = 95.0

# `game_type` values for games that are not competitive baseball: spring
# training, exhibitions, and the All-Star game. Postseason codes are deliberately
# not enumerated here, so filtering on this set keeps them.
#
# This matters more than it looks. Statcast **computes no expected statistics for
# spring training**: all 13 spring batted balls in the probe frame carried a null
# `estimated_ba_using_speedangle`, against 1 of 143 in the regular season
# (2026-08-09). So a category that mixes spring games into a sample gets the
# worst of both, counting spring plate appearances in a denominator while their
# expected-stat numerators silently vanish. Spring was 7.8% of the probe batter's
# pitches and 10.1% of the probe pitcher's.
#
# Only `R` and `S` were observed in the two probe frames, so the members beyond
# `S` are named from the MLB Stats API's documented vocabulary rather than
# measured here.
NON_COMPETITIVE_GAME_TYPES = frozenset({"S", "E", "A"})


# ---------------------------------------------------------------------------
# Statcast `pitch_type` grouping
# ---------------------------------------------------------------------------
#
# Statcast `pitch_type` codes grouped the way Baseball Savant's own pitch-group
# views do. The ROADMAP names the members of two of the three groups directly
# ("Slider/Sweeper/Curve", "Changeup/Splitter"); the fastball group is the one
# worth defending.
#
# The cutter (FC) is counted as a fastball. It is the arguable call (a cutter
# breaks, and some classification schemes file it with the sliders) but Savant
# groups it under fastballs, it is thrown at fastball velocity, and the measured
# league sample bears out the grouping: mean |pfx_x| of 0.25 ft puts FC nearer
# the four-seamer (0.67) than the slider (0.36) is to the sweeper (1.11), and its
# mean vertical approach angle (-6.03) sits between the sinker (-5.77) and the
# breaking balls (-7.5 and steeper). Reclassifying it moves roughly 9 percent of
# league pitches, so this is a decision to revisit deliberately, not silently.

FASTBALL_TYPES = frozenset({"FF", "SI", "FC"})
BREAKING_TYPES = frozenset({"SL", "ST", "CU", "KC", "SV", "CS"})
OFFSPEED_TYPES = frozenset({"CH", "FS", "FO"})

PITCH_CLASS_FASTBALL = "fastball"
PITCH_CLASS_BREAKING = "breaking"
PITCH_CLASS_OFFSPEED = "offspeed"

_CLASS_BY_TYPE = {
    **dict.fromkeys(FASTBALL_TYPES, PITCH_CLASS_FASTBALL),
    **dict.fromkeys(BREAKING_TYPES, PITCH_CLASS_BREAKING),
    **dict.fromkeys(OFFSPEED_TYPES, PITCH_CLASS_OFFSPEED),
}


# ---------------------------------------------------------------------------
# Statcast `zone` codes
# ---------------------------------------------------------------------------
#
# Statcast's own `zone`, used as published. Codes 1-9 are the 3x3 in-zone grid
# CALC_30 asks for; codes 11-14 are the four out-of-zone quadrants. There is no
# code 10. See the source module for why these are not reconstructed from
# `plate_x` / `plate_z`.

IN_ZONE_CODES = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})
OUT_OF_ZONE_CODES = frozenset({11, 12, 13, 14})


def pitch_class(pitch_type: str | None) -> str | None:
    """Group a Statcast `pitch_type` code into fastball / breaking / offspeed.

    Returns None for a missing code and for the handful that belong to no group
    (pitchouts, intentional balls, eephus, knuckleballs, and Statcast's own
    "unknown"). They are 0.1 percent of league pitches and they are excluded
    rather than assigned, so a usage share reflects only what was classified.
    """
    return _CLASS_BY_TYPE.get(pitch_type)


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


def is_competitive(record: dict[str, Any]) -> bool:
    """Whether *record* comes from a competitive game.

    A record with no ``game_type`` key is treated as competitive: the column is
    absent from some field projections, and the alternative would silently empty
    every rate for any category that has not opted in.
    """
    return record.get("game_type") not in NON_COMPETITIVE_GAME_TYPES


def terminal_pitch_by_pa(
    pitches: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reduce pitch rows to the one pitch that ended each plate appearance.

    Grouped on ``(game_pk, at_bat_number)`` -- the pair that identifies a plate
    appearance -- and reduced by maximum ``pitch_number`` within the group.

    Lives here because two categories need it and a second copy would eventually
    disagree with the first. Use it before filtering pitch rows on any attribute
    that varies within a plate appearance (velocity, pitch type, break, the count
    itself). Filtering first and grouping second leaves every plate appearance
    whose *terminal* pitch fell outside the filter in the denominator carrying
    ``events: None``, which reads as an out; that is the CALC_14 defect in issue
    #37.

    Verified against Statcast: the maximum-``pitch_number`` row of every plate
    appearance is exactly the row carrying a terminal ``events`` value (240 of
    240 plate appearances, 2026-08-08).

    Rows missing any of the three identifying fields are dropped rather than
    merged into one bogus group or defaulted to pitch zero.
    """
    terminal: dict[tuple[Any, Any], dict[str, Any]] = {}
    for pitch in pitches:
        game_pk = pitch.get("game_pk")
        at_bat = pitch.get("at_bat_number")
        number = pitch.get("pitch_number")
        if game_pk is None or at_bat is None or number is None:
            continue
        key = (game_pk, at_bat)
        current = terminal.get(key)
        if current is None or number > current["pitch_number"]:
            terminal[key] = pitch
    return list(terminal.values())


def empty_line() -> dict[str, int]:
    """Return a zeroed counting-stat line."""
    return dict.fromkeys(COUNTING_STATS, 0)


def aggregate_lines(lines: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Sum *lines* field by field, treating missing keys as 0."""
    total = empty_line()
    for line in lines:
        for stat in COUNTING_STATS:
            total[stat] += line.get(stat, 0) or 0
    return total


def rate_or_none(numerator: float, denominator: int) -> Rate | None:
    """Return a `Rate`, or ``None`` when there is nothing to divide by.

    An empty sample is absence of evidence, not a 0.0 rate, and callers have to
    be able to tell the two apart. *numerator* is a float because some
    calculators average per-event estimates rather than counting occurrences.
    """
    if denominator <= 0:
        return None
    return Rate(numerator / denominator, denominator)


# ---------------------------------------------------------------------------
# Window — the temporal scope a calculator operates over
# ---------------------------------------------------------------------------
# Each variant is a public frozen dataclass so consumers dispatch cleanly via
# isinstance and the constructor doubles as the value:
#
#   window = DAYS(14)
#   isinstance(window, DAYS)  # True
#   window.n                  # 14
#
# CAREER and SEASON take no arguments; the others take a single `n`.


@dataclass(frozen=True)
class CAREER:
    """All available data; no temporal restriction."""


@dataclass(frozen=True)
class SEASON:
    """Records from the current season only."""


@dataclass(frozen=True)
class DAYS:
    """The last *n* calendar days ending yesterday.

    Today is excluded because today's game has not been played yet. An empty
    window (no records inside the range) returns ``None`` from
    ``apply_window``, not a zero-rate sample.
    """

    n: int


@dataclass(frozen=True)
class GAMES:
    """The last *n* distinct games, appearance-anchored.

    Unaffected by calendar gaps: a player who missed 30 days but played 5 games
    before that absence still yields 5 games under ``GAMES(5)``.
    """

    n: int


@dataclass(frozen=True)
class PLATE_APPEARANCES:
    """The last *n* plate appearances, appearance-anchored.

    Expects *one record per plate appearance* — pitch-level callers must
    pre-group to PA records before passing them here. Sorted internally by
    ``date_field`` so input order does not matter.
    """

    n: int


@dataclass(frozen=True)
class SEASONS:
    """The last *n* seasons, inclusive of the anchor year.

    The anchor defaults to the current calendar year. Pass ``anchor_year`` to
    ``apply_window`` when the caller has an explicit season in scope (e.g.,
    ``calc_03`` computing a 3-year BvP window ending in a specific season).
    """

    n: int


# Union type for annotations — all six window kinds.
Window = CAREER | SEASON | DAYS | GAMES | PLATE_APPEARANCES | SEASONS


# ---------------------------------------------------------------------------
# apply_window — slice a record list to the subset a Window describes
# ---------------------------------------------------------------------------


def _parse_game_date(value: Any) -> datetime.date | None:
    """Parse a ``YYYY-MM-DD`` string to a date, returning ``None`` on failure."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError:
        return None


def apply_window(
    records: Sequence[dict[str, Any]],
    window: Window,
    *,
    today: datetime.date | None = None,
    anchor_year: int | None = None,
    date_field: str = "game_date",
    season_field: str = "season",
) -> list[dict[str, Any]] | None:
    """Slice *records* to the subset described by *window*.

    Returns ``None`` rather than ``[]`` when the window contains no records —
    an empty window is absence of evidence, not a zero-rate sample.

    Args:
        records: game or pitch records, each a dict. Date-based windows
            (``DAYS``, ``GAMES``, ``PLATE_APPEARANCES``) expect *date_field*
            as a ``"YYYY-MM-DD"`` string. ``SEASONS`` and ``SEASON`` windows
            expect *season_field* as an ``int`` (falling back to year of
            *date_field* when *season_field* is absent).
        window: one of ``CAREER()``, ``SEASON()``, ``DAYS(n)``, ``GAMES(n)``,
            ``PLATE_APPEARANCES(n)``, or ``SEASONS(n)``.
        today: override today's date (for testing). Defaults to
            ``datetime.date.today()``.
        anchor_year: override the latest year for ``SEASONS(n)`` and
            ``SEASON()``. Defaults to ``today.year``. Pass the caller's
            explicit ``season`` parameter here so that
            ``apply_window(records, SEASONS(3), anchor_year=2024)`` correctly
            selects 2022-2024 rather than anchoring to the calendar year.
        date_field: key for the game-date string in each record.
        season_field: key for the season integer in each record.
    """
    if today is None:
        today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    current_year = anchor_year if anchor_year is not None else today.year

    if isinstance(window, CAREER):
        result = list(records)

    elif isinstance(window, SEASON):
        filtered = []
        for r in records:
            if season_field in r:
                if r[season_field] == current_year:
                    filtered.append(r)
            else:
                d = _parse_game_date(r.get(date_field))
                if d is not None and d.year == current_year:
                    filtered.append(r)
        result = filtered

    elif isinstance(window, DAYS):
        earliest = yesterday - datetime.timedelta(days=window.n - 1)
        result = [
            r
            for r in records
            if (d := _parse_game_date(r.get(date_field))) is not None
            and earliest <= d <= yesterday
        ]

    elif isinstance(window, GAMES):
        # Sort ascending so reversed() walks most-recent first.
        sorted_recs = sorted(
            records,
            key=lambda r: _parse_game_date(r.get(date_field)) or datetime.date.min,
        )
        seen: list[str] = []
        for r in reversed(sorted_recs):
            gd = r.get(date_field)
            if gd and gd not in seen:
                seen.append(gd)
            if len(seen) >= window.n:
                break
        cutoff = set(seen)
        result = [r for r in sorted_recs if r.get(date_field) in cutoff]

    elif isinstance(window, PLATE_APPEARANCES):
        sorted_recs = sorted(
            records,
            key=lambda r: _parse_game_date(r.get(date_field)) or datetime.date.min,
        )
        result = sorted_recs[-window.n :]

    elif isinstance(window, SEASONS):
        earliest_season = current_year - window.n + 1
        result = [
            r
            for r in records
            if earliest_season <= int(r.get(season_field) or 0) <= current_year
        ]

    else:
        # Exhaustive — this branch is unreachable if Window is a closed union.
        result = list(records)

    return result if result else None


# ---------------------------------------------------------------------------
# Role — the semantic meaning of a calculator's returned value
# ---------------------------------------------------------------------------
# Each variant is a public frozen dataclass wrapping a Rate, so consumers
# dispatch cleanly via isinstance and the constructor documents intent:
#
#   return PROBABILITY(rate_or_none(hits, pa))
#
# The four roles map onto the composite model (CALC_75):
#   PROBABILITY  → blends directly into p_hit (a batting-average-style rate)
#   MULTIPLIER   → scales p_hit; must not be read as a probability
#   EXPONENT     → feeds PA_proj (e.g. lineup-spot PA expectation)
#   DELTA        → signed rate adjustment; may be negative, not clamped to [0, 1]
#
# The caller contract is ``PROBABILITY | None`` — ``None`` when there is no
# sample, a role-tagged Rate when there is. ``rate_or_none`` is unchanged;
# callers compose it:
#
#   value = rate_or_none(hits, pa)
#   return PROBABILITY(value) if value is not None else None


@dataclass(frozen=True)
class PROBABILITY:
    """A batting-average style rate that blends into p_hit."""

    value: Rate


@dataclass(frozen=True)
class MULTIPLIER:
    """A scaling factor applied to p_hit; must not be read as a probability."""

    value: Rate


@dataclass(frozen=True)
class EXPONENT:
    """A projected plate-appearance count that feeds PA_proj, not p_hit."""

    value: Rate


@dataclass(frozen=True)
class DELTA:
    """A signed rate adjustment that may be negative and is not clamped to [0, 1]."""

    value: Rate


# Union type for annotations — all four role kinds.
Role = PROBABILITY | MULTIPLIER | EXPONENT | DELTA
