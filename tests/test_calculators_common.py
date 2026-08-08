import datetime

import pytest

from calculators import aggregate_lines, apply_window, rate_or_none
from calculators.common import (
    CAREER,
    DAYS,
    DELTA,
    EXPONENT,
    GAMES,
    MULTIPLIER,
    PLATE_APPEARANCES,
    PROBABILITY,
    Rate,
    Role,
    SEASON,
    SEASONS,
    Window,
)

# Pin "today" for all window tests so the tests stay deterministic.
TODAY = datetime.date(2026, 8, 8)
# yesterday = 2026-08-07
# DAYS(14) window: [2026-07-25, 2026-08-07]


# ---- aggregate_lines ----


def test_aggregate_lines_tolerates_missing_and_null_fields():
    assert aggregate_lines([{"hits": 2}, {"hits": None, "atBats": 3}]) == {
        "plateAppearances": 0,
        "atBats": 3,
        "hits": 2,
        "strikeOuts": 0,
        "baseOnBalls": 0,
    }


# ---- helpers ----


def _game(date: str, hits: int = 1, pa: int = 4) -> dict:
    """Minimal game/PA record keyed by game_date."""
    return {"game_date": date, "hits": hits, "plateAppearances": pa}


def _season_rec(season: int, hits: int = 5, pa: int = 20) -> dict:
    """Minimal per-season split record keyed by season int."""
    return {"season": season, "hits": hits, "plateAppearances": pa}


# ---- apply_window: DAYS ----


class TestDaysWindow:
    """DAYS(n) — the n calendar days ending yesterday (today excluded)."""

    def test_il_return_all_games_outside_window_returns_none(self):
        """Player just back from IL: most recent game was 20 days ago → None."""
        records = [
            _game("2026-07-15"),  # 24 days before today
            _game("2026-07-19"),  # 20 days before today — still outside DAYS(14)
        ]
        assert apply_window(records, DAYS(14), today=TODAY) is None

    def test_single_game_in_window_carries_correct_denominator(self):
        """One game in DAYS(14) range; denominator must reflect that game only."""
        records = [
            _game("2026-07-19"),  # 20 days ago — outside window
            _game("2026-08-02", hits=1, pa=4),  # 6 days ago — inside window
        ]
        sliced = apply_window(records, DAYS(14), today=TODAY)
        assert sliced is not None
        assert len(sliced) == 1
        # Aggregate and confirm the denominator is from the one in-range game.
        line = aggregate_lines(sliced)
        result = rate_or_none(line["hits"], line["plateAppearances"])
        assert result is not None
        assert result.denominator == 4

    def test_empty_records_returns_none(self):
        assert apply_window([], DAYS(14), today=TODAY) is None

    def test_today_is_excluded(self):
        """A record dated today is outside the window (game not yet played)."""
        records = [
            _game("2026-08-08"),  # today — excluded
            _game("2026-08-07"),  # yesterday — included
        ]
        result = apply_window(records, DAYS(14), today=TODAY)
        assert result is not None
        dates = {r["game_date"] for r in result}
        assert "2026-08-08" not in dates
        assert "2026-08-07" in dates

    @pytest.mark.parametrize(
        "date",
        [
            "2026-07-25",  # earliest day in DAYS(14): yesterday - 13 = 2026-07-25
            "2026-08-07",  # yesterday: latest day in the window
        ],
    )
    def test_boundary_dates_are_included(self, date: str):
        """Both ends of the DAYS(14) window are inclusive."""
        result = apply_window([_game(date)], DAYS(14), today=TODAY)
        assert result is not None
        assert result[0]["game_date"] == date

    def test_day_before_earliest_boundary_is_excluded(self):
        """2026-07-24 is one day outside the DAYS(14) window."""
        result = apply_window([_game("2026-07-24")], DAYS(14), today=TODAY)
        assert result is None


# ---- apply_window: GAMES ----


class TestGamesWindow:
    """GAMES(n) — last n distinct games, appearance-anchored (calendar gap irrelevant)."""

    def test_calendar_gap_does_not_affect_game_count(self):
        """Player had a 49-day IL stint; GAMES(3) still returns the 3 most-recent games."""
        records = [
            _game("2026-06-01"),  # 4th most recent — excluded
            _game("2026-06-10"),  # 3rd most recent — included
            _game("2026-06-20"),  # 2nd most recent — included
            # 49-day calendar gap (IL stint)
            _game("2026-08-07"),  # most recent — included
        ]
        result = apply_window(records, GAMES(3), today=TODAY)
        assert result is not None
        dates = {r["game_date"] for r in result}
        assert "2026-06-01" not in dates
        assert dates == {"2026-06-10", "2026-06-20", "2026-08-07"}

    def test_all_records_for_matching_date_are_returned(self):
        """Two records sharing the latest game date are both included under GAMES(1)."""
        records = [
            _game("2026-07-01"),
            _game("2026-07-02"),
            _game("2026-07-02"),  # second record same date
        ]
        result = apply_window(records, GAMES(1), today=TODAY)
        assert result is not None
        assert len(result) == 2
        assert all(r["game_date"] == "2026-07-02" for r in result)

    def test_unsorted_input_still_selects_most_recent(self):
        """GAMES sorts internally; descending input returns the same result."""
        ascending = [_game("2026-07-01"), _game("2026-07-10"), _game("2026-07-20")]
        descending = list(reversed(ascending))
        assert apply_window(ascending, GAMES(2), today=TODAY) == apply_window(
            descending, GAMES(2), today=TODAY
        )

    def test_empty_records_returns_none(self):
        assert apply_window([], GAMES(5), today=TODAY) is None


# ---- apply_window: PLATE_APPEARANCES ----


class TestPlateAppearancesWindow:
    """PLATE_APPEARANCES(n) — last n records, sorted internally by date."""

    def test_returns_last_n_records_by_date(self):
        records = [_game(f"2026-07-0{i}") for i in range(1, 8)]  # 7 records
        result = apply_window(records, PLATE_APPEARANCES(3), today=TODAY)
        assert result is not None
        assert len(result) == 3
        dates = [r["game_date"] for r in result]
        # Most-recent three dates, sorted ascending.
        assert dates == ["2026-07-05", "2026-07-06", "2026-07-07"]

    def test_unsorted_input_still_takes_most_recent(self):
        records = [
            _game("2026-07-20"),
            _game("2026-07-01"),
            _game("2026-07-10"),
        ]
        result = apply_window(records, PLATE_APPEARANCES(2), today=TODAY)
        assert result is not None
        dates = {r["game_date"] for r in result}
        assert dates == {"2026-07-10", "2026-07-20"}

    def test_empty_records_returns_none(self):
        assert apply_window([], PLATE_APPEARANCES(5), today=TODAY) is None


# ---- apply_window: CAREER ----


class TestCareerWindow:
    """CAREER() — all records, no restriction."""

    def test_career_returns_all_records(self):
        records = [_game("2024-07-01"), _game("2025-07-01"), _game("2026-07-01")]
        result = apply_window(records, CAREER(), today=TODAY)
        assert result is not None
        assert len(result) == 3

    def test_career_empty_records_returns_none(self):
        assert apply_window([], CAREER(), today=TODAY) is None


# ---- apply_window: SEASONS ----


class TestSeasonsWindow:
    """SEASONS(n) — last n seasons, anchored to anchor_year (or today.year)."""

    def test_filters_to_last_n_seasons_from_today(self):
        records = [
            _season_rec(2023),
            _season_rec(2024),
            _season_rec(2025),
            _season_rec(2026),
        ]
        result = apply_window(records, SEASONS(3), today=TODAY)
        assert result is not None
        seasons = {r["season"] for r in result}
        assert seasons == {2024, 2025, 2026}
        assert 2023 not in seasons

    def test_anchor_year_controls_window_end(self):
        """anchor_year=2024 makes SEASONS(3) select 2022-2024, not 2024-2026."""
        records = [
            _season_rec(2022),
            _season_rec(2023),
            _season_rec(2024),
            _season_rec(2025),
            _season_rec(2026),
        ]
        result = apply_window(records, SEASONS(3), today=TODAY, anchor_year=2024)
        assert result is not None
        seasons = {r["season"] for r in result}
        assert seasons == {2022, 2023, 2024}

    def test_empty_records_returns_none(self):
        assert apply_window([], SEASONS(3), today=TODAY) is None


# ---- apply_window: SEASON ----


class TestSeasonWindow:
    """SEASON() — current season (or anchor_year) only."""

    def test_season_filters_to_current_year_by_season_field(self):
        records = [_season_rec(2025), _season_rec(2026), _season_rec(2026)]
        result = apply_window(records, SEASON(), today=TODAY)
        assert result is not None
        assert all(r["season"] == 2026 for r in result)
        assert len(result) == 2

    def test_season_falls_back_to_game_date_year(self):
        """Records without a season field use the year from game_date."""
        records = [
            _game("2025-09-01"),
            _game("2026-07-15"),
            _game("2026-08-01"),
        ]
        result = apply_window(records, SEASON(), today=TODAY)
        assert result is not None
        assert all(r["game_date"].startswith("2026") for r in result)
        assert len(result) == 2

    def test_season_empty_records_returns_none(self):
        assert apply_window([], SEASON(), today=TODAY) is None


# ---- Role types ----


class TestRoleTypes:
    """Role-tagged calculator returns: PROBABILITY, MULTIPLIER, EXPONENT, DELTA."""

    def test_probability_carries_rate(self):
        r = PROBABILITY(Rate(0.3, 5))
        assert r.value.rate == pytest.approx(0.3)
        assert r.value.denominator == 5

    def test_multiplier_carries_rate(self):
        r = MULTIPLIER(Rate(1.2, 10))
        assert r.value.rate == pytest.approx(1.2)
        assert r.value.denominator == 10

    def test_exponent_carries_rate(self):
        r = EXPONENT(Rate(4.1, 9))
        assert r.value.rate == pytest.approx(4.1)
        assert r.value.denominator == 9

    def test_delta_carries_rate(self):
        # DELTA may be negative — not clamped to [0, 1].
        r = DELTA(Rate(-0.05, 20))
        assert r.value.rate == pytest.approx(-0.05)
        assert r.value.denominator == 20

    @pytest.mark.parametrize(
        "role_value, expected_type",
        [
            (PROBABILITY(Rate(0.3, 5)), PROBABILITY),
            (MULTIPLIER(Rate(1.2, 10)), MULTIPLIER),
            (EXPONENT(Rate(4.1, 9)), EXPONENT),
            (DELTA(Rate(-0.05, 20)), DELTA),
        ],
    )
    def test_roles_are_distinguishable_by_isinstance(
        self, role_value: Role, expected_type: type
    ) -> None:
        """Each role kind is uniquely identifiable by isinstance, no lookup table needed."""
        assert isinstance(role_value, expected_type)
        all_types = [PROBABILITY, MULTIPLIER, EXPONENT, DELTA]
        for t in all_types:
            if t is expected_type:
                continue
            assert not isinstance(role_value, t)

    def test_consumer_can_branch_on_role(self) -> None:
        """A consumer dispatches on role via isinstance without a per-calculator lookup."""
        roles: list[Role] = [
            PROBABILITY(Rate(0.3, 5)),
            MULTIPLIER(Rate(1.1, 30)),
            EXPONENT(Rate(4.2, 9)),
            DELTA(Rate(-0.02, 15)),
        ]
        seen = []
        for role in roles:
            if isinstance(role, PROBABILITY):
                seen.append("prob")
            elif isinstance(role, MULTIPLIER):
                seen.append("mult")
            elif isinstance(role, EXPONENT):
                seen.append("exp")
            elif isinstance(role, DELTA):
                seen.append("delta")
        assert seen == ["prob", "mult", "exp", "delta"]

    def test_none_propagates_as_absent_sample(self) -> None:
        """rate_or_none returns None for zero denominator; callers wrap only non-None."""
        raw = rate_or_none(0.0, 0)  # no sample
        assert raw is None
        # The caller contract: PROBABILITY | None
        result: PROBABILITY | None = PROBABILITY(raw) if raw is not None else None
        assert result is None

    def test_sample_size_survives_role_wrapper(self) -> None:
        """The denominator is accessible through the role wrapper's .value field."""
        raw = rate_or_none(3, 10)
        assert raw is not None
        tagged = PROBABILITY(raw)
        assert tagged.value.denominator == 10
        assert tagged.value.rate == pytest.approx(0.3)


# ---- Window type discrimination ----


@pytest.mark.parametrize(
    "window, expected_type",
    [
        (CAREER(), CAREER),
        (SEASON(), SEASON),
        (DAYS(14), DAYS),
        (GAMES(5), GAMES),
        (PLATE_APPEARANCES(50), PLATE_APPEARANCES),
        (SEASONS(3), SEASONS),
    ],
)
def test_window_variants_are_distinguishable_by_isinstance(
    window: Window, expected_type: type
):
    """Each Window kind is uniquely identifiable by isinstance checks."""
    assert isinstance(window, expected_type)
    # None of the other five match.
    all_types = [CAREER, SEASON, DAYS, GAMES, PLATE_APPEARANCES, SEASONS]
    for t in all_types:
        if t is expected_type:
            continue
        assert not isinstance(window, t)
