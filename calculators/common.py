"""Types and helpers shared by every calculator category.

Nothing here is specific to one category, and nothing here touches the network.
"""

import datetime
from dataclasses import dataclass
from typing import Any, Iterable, NamedTuple, Sequence


# Counting stats carried through from a MLB Stats API hitting split. Every
# category that divides one count by another draws its terms from this set.
COUNTING_STATS = ("plateAppearances", "atBats", "hits", "strikeOuts", "baseOnBalls")


class Rate(NamedTuple):
    """A rate paired with the sample size it was computed over.

    `denominator` travels with the rate because most of the model's inputs are
    small samples — a 1-for-2 head-to-head line is not evidence of a .500
    hitter. The composite model (CALC_75) needs the count to shrink the rate
    toward a prior, so no calculator returns a bare float.
    """

    rate: float
    denominator: int


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
