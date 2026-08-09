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
