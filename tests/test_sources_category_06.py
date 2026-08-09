"""Tests for calculators/sources/category_06_lineup_game_context.py."""

from datetime import date

import pytest
import requests

from calculators.sources.category_06_lineup_game_context import (
    CATEGORY_06_BATTER_FIELDS,
    CATEGORY_06_PITCHER_FIELDS,
    fetch_batter_tto,
    fetch_lineup_rates,
    fetch_pitcher_tto,
    lineup_by_spot,
)
from tests.conftest import FakeResponse, _install_fake_pybaseball, _statcast_frame


def _people_payload(*players):
    """A hydrated /people response. *players* are (id, obp, slg, ops, pa)."""
    return {
        "people": [
            {
                "id": pid,
                "stats": [
                    {
                        "splits": [
                            {
                                "stat": {
                                    "obp": obp,
                                    "slg": slg,
                                    "ops": ops,
                                    "plateAppearances": pa,
                                }
                            }
                        ]
                    }
                ],
            }
            for pid, obp, slg, ops, pa in players
        ]
    }


def _rows(n=2, **overrides):
    base = {
        "game_date": "2026-07-01",
        "game_pk": 745000,
        "game_type": "R",
        "at_bat_number": 1,
        "pitch_number": 1,
        "events": None,
        "inning": 1,
        "outs_when_up": 0,
        "batter": 592450,
        "pitcher": 668678,
        "arm_angle": 42.0,  # not a Category 6 field; must be projected away
    }
    base.update(overrides)
    return [dict(base, pitch_number=i + 1) for i in range(n)]


# ---------------------------------------------------------------------------
# fetch_lineup_rates
# ---------------------------------------------------------------------------


def test_one_request_covers_the_whole_batting_order(monkeypatch):
    """CALC_42 and CALC_43 each need a *different* hitter's line than the one
    being evaluated, so per-player fetching would pull every line twice."""
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(params)
        return FakeResponse(
            _people_payload(
                *[(pid, ".340", ".420", ".760", 400) for pid in range(1, 10)]
            )
        )

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch_lineup_rates(range(1, 10), 2026)
    assert len(calls) == 1
    assert len(result) == 9


def test_the_request_hydrates_the_season_hitting_line(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_people_payload((1, ".340", ".420", ".760", 400)))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_lineup_rates([1], 2024)
    assert "stats(group=[hitting],type=[season],season=2024)" == seen["hydrate"]


def test_the_ids_are_deduplicated_and_sorted(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_people_payload((1, ".340", ".420", ".760", 400)))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_lineup_rates([3, 1, 3, 2])
    assert seen["personIds"] == "1,2,3"


def test_the_rate_strings_are_passed_through_unparsed(monkeypatch):
    """The API serves ".375", and parsing belongs with the calculators, which is
    where the ".---" empty-line case is handled."""
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResponse(_people_payload((1, ".375", ".533", ".908", 261))),
    )
    assert fetch_lineup_rates([1])[1]["obp"] == ".375"


def test_a_player_with_no_hitting_line_is_absent_rather_than_empty(monkeypatch):
    """A pitcher id resolves to nothing, which is the right shape for a caller
    that passes a whole roster without filtering first."""
    payload = {"people": [{"id": 668678, "stats": []}]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_lineup_rates([668678]) == {}


def test_the_season_defaults_to_the_current_year(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen.update(params or {})
        return FakeResponse(_people_payload((1, ".340", ".420", ".760", 400)))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_lineup_rates([1])
    assert str(date.today().year) in seen["hydrate"]


def test_an_empty_id_collection_makes_no_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: calls.append(1) or FakeResponse({})
    )
    assert fetch_lineup_rates([]) == {}
    assert calls == []


def test_a_failing_request_returns_an_empty_mapping(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_lineup_rates([1, 2]) == {}


def test_an_http_error_returns_an_empty_mapping(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse({}, status_code=500)
    )
    assert fetch_lineup_rates([1, 2]) == {}


# ---------------------------------------------------------------------------
# lineup_by_spot
# ---------------------------------------------------------------------------


def test_the_rekeying_turns_player_ids_into_batting_order_spots():
    """The calculators never see a player id, only a spot."""
    rates = {101: {"obp": ".375"}, 102: {"obp": ".300"}}
    assert lineup_by_spot({1: 101, 2: 102}, rates) == {
        1: {"obp": ".375"},
        2: {"obp": ".300"},
    }


def test_a_spot_whose_hitter_has_no_line_is_dropped():
    assert lineup_by_spot({1: 101, 2: 999}, {101: {"obp": ".375"}}) == {
        1: {"obp": ".375"}
    }


@pytest.mark.parametrize("spots", [None, {}])
def test_rekeying_without_spots_is_empty(spots):
    assert lineup_by_spot(spots, {101: {"obp": ".375"}}) == {}


# ---------------------------------------------------------------------------
# The two Statcast field tuples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["inning", "outs_when_up", "batter"])
def test_the_pitcher_tuple_carries_what_the_start_test_and_bucketing_need(field):
    assert field in CATEGORY_06_PITCHER_FIELDS


@pytest.mark.parametrize("field", ["inning", "pitcher"])
def test_the_batter_tuple_carries_what_the_starter_guard_needs(field):
    assert field in CATEGORY_06_BATTER_FIELDS


def test_the_batter_tuple_deliberately_omits_outs_when_up():
    """The batter side cannot use the start test at all, so the column that
    exists to serve it would be dead weight and would invite reuse of a rule that
    silently destroys the sample."""
    assert "outs_when_up" not in CATEGORY_06_BATTER_FIELDS


@pytest.mark.parametrize("field", list(CATEGORY_06_PITCHER_FIELDS))
def test_every_pitcher_field_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame(_rows(1)))
    assert field in fetch_pitcher_tto(668678, 2026)[0]


@pytest.mark.parametrize("field", list(CATEGORY_06_BATTER_FIELDS))
def test_every_batter_field_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert field in fetch_batter_tto(592450, 2026)[0]


def test_columns_outside_the_field_set_are_dropped(monkeypatch):
    _install_fake_pybaseball(monkeypatch, pitcher_frame=_statcast_frame(_rows(1)))
    assert "arm_angle" not in fetch_pitcher_tto(668678, 2026)[0]


def test_spring_training_rows_survive_the_fetch(monkeypatch):
    """The fetcher does not filter; excluding non-competitive games is the
    calculators' job."""
    _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1, game_type="S"))
    )
    assert fetch_pitcher_tto(668678, 2026)[0]["game_type"] == "S"


# ---------------------------------------------------------------------------
# Side selection, cache sharing, error contract
# ---------------------------------------------------------------------------


def test_each_fetcher_pulls_its_own_side(monkeypatch):
    calls = _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1)),
        pitcher_frame=_statcast_frame(_rows(1)),
    )
    fetch_pitcher_tto(668678, 2024)
    fetch_batter_tto(592450, 2024)
    assert calls == [
        ("pitcher", "2024-01-01", "2024-12-31", 668678),
        ("2024-01-01", "2024-12-31", 592450),
    ]


def test_the_category_9_fetcher_reuses_the_same_cached_frame(monkeypatch):
    """Different field projections, one network call."""
    from calculators.sources.category_09_pitcher_form import fetch_pitcher_form

    calls = _install_fake_pybaseball(
        monkeypatch, pitcher_frame=_statcast_frame(_rows(1))
    )
    category_06 = fetch_pitcher_tto(668678, 2026)
    fetch_pitcher_form(668678, 2026)
    assert len(calls) == 1
    assert "batter" in category_06[0]


@pytest.mark.parametrize(
    "fetcher,player", [(fetch_pitcher_tto, 668678), (fetch_batter_tto, 592450)]
)
def test_a_pre_statcast_season_returns_empty_without_a_network_call(
    fetcher, player, monkeypatch
):
    calls = _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1)),
        pitcher_frame=_statcast_frame(_rows(1)),
    )
    assert fetcher(player, 2014) == []
    assert calls == []


@pytest.mark.parametrize(
    "fetcher,player", [(fetch_pitcher_tto, 668678), (fetch_batter_tto, 592450)]
)
def test_a_failing_pull_returns_empty_rather_than_raising(fetcher, player, monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant is down"))
    assert fetcher(player, 2026) == []


@pytest.mark.parametrize("frame", [None, "empty"])
def test_an_empty_or_missing_frame_returns_empty(frame, monkeypatch):
    empty = _statcast_frame([]) if frame == "empty" else None
    _install_fake_pybaseball(monkeypatch, frame=empty, pitcher_frame=empty)
    assert fetch_pitcher_tto(668678, 2026) == []
    assert fetch_batter_tto(592450, 2026) == []
