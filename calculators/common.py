"""Types and helpers shared by every calculator category.

Nothing here is specific to one category, and nothing here touches the network.
"""

from typing import Any, Iterable, NamedTuple


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


def rate_or_none(numerator: int, denominator: int) -> Rate | None:
    """Return a `Rate`, or ``None`` when there is nothing to divide by.

    An empty sample is absence of evidence, not a 0.0 rate, and callers have to
    be able to tell the two apart.
    """
    if denominator <= 0:
        return None
    return Rate(numerator / denominator, denominator)
