"""Tests for calculators/sources/category_05_ballpark_environment.py."""

from datetime import date

import pytest
import requests

from calculators.sources import category_05_ballpark_environment as sources
from calculators.sources.category_05_ballpark_environment import (
    CATEGORY_05_PITCH_FIELDS,
    SITUATIONAL_SIT_CODES,
    empty_environment,
    environment_from_schedule,
    fetch_batter_spray,
    fetch_game_environment,
    fetch_situational_splits,
)
from tests.conftest import FakeResponse, _install_fake_pybaseball, _statcast_frame

ENVIRONMENT_KEYS = {
    "venue_id",
    "weather",
    "wind",
    "day_night",
    "home_team",
    "batter_is_home",
}


def _rows(n=2, **overrides):
    base = {
        "game_date": "2026-07-01",
        "game_pk": 745000,
        "game_type": "R",
        "at_bat_number": 1,
        "pitch_number": 1,
        "events": None,
        "description": "ball",
        "bb_type": None,
        "hc_x": None,
        "hc_y": None,
        "home_team": "NYY",
        "away_team": "BOS",
        "arm_angle": 42.0,  # not a Category 5 field; must be projected away
    }
    base.update(overrides)
    return [dict(base, pitch_number=i + 1) for i in range(n)]


def _boxscore(weather="89 degrees, Partly Cloudy.", wind="11 mph, Out To RF."):
    info = []
    if weather is not None:
        info.append({"label": "Weather", "value": weather})
    if wind is not None:
        info.append({"label": "Wind", "value": wind})
    info.append({"label": "Venue", "value": "Yankee Stadium"})
    return {"info": info, "teams": {}}


# ---------------------------------------------------------------------------
# The empty-record contract
# ---------------------------------------------------------------------------


def test_the_empty_environment_carries_every_key():
    """Every key present and None, so `compute_category_05` reads the record
    without guarding each field."""
    record = empty_environment()
    assert set(record) == ENVIRONMENT_KEYS
    assert all(value is None for value in record.values())


# ---------------------------------------------------------------------------
# fetch_game_environment
# ---------------------------------------------------------------------------


def test_the_weather_and_wind_strings_are_passed_through_unparsed(monkeypatch):
    """Parsing them is pure string work and belongs with the calculators."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(_boxscore()))
    record = fetch_game_environment(745000)
    assert record["weather"] == "89 degrees, Partly Cloudy."
    assert record["wind"] == "11 mph, Out To RF."


def test_it_reads_the_boxscore_for_the_requested_game(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return FakeResponse(_boxscore())

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_game_environment(745123)
    assert seen["url"].endswith("/game/745123/boxscore")


@pytest.mark.parametrize("missing", ["weather", "wind"])
def test_a_field_absent_before_first_pitch_is_none(missing, monkeypatch):
    """Measured 2026-08-09: 6 of 8 Preview-state games carried no weather at all.
    The absence must be expressible, not filled in."""
    payload = _boxscore(**{missing: None})
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert fetch_game_environment(745000)[missing] is None


def test_an_empty_info_list_yields_a_none_filled_record(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse({"info": []}))
    assert fetch_game_environment(745000) == empty_environment()


def test_a_failing_request_returns_the_empty_record_rather_than_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("savant is down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_game_environment(745000) == empty_environment()


def test_an_http_error_returns_the_empty_record(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResponse({}, status_code=404)
    )
    assert fetch_game_environment(745000) == empty_environment()


def test_the_boxscore_fetch_does_not_supply_the_schedule_keys(monkeypatch):
    """venue_id and day_night come from the schedule, which publishes them as
    soon as the game exists; the weather does not."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(_boxscore()))
    record = fetch_game_environment(745000)
    assert record["venue_id"] is None
    assert record["day_night"] is None


# ---------------------------------------------------------------------------
# environment_from_schedule
# ---------------------------------------------------------------------------


def _game(venue_id=3313, day_night="night", abbreviation="NYY"):
    return {
        "gamePk": 745000,
        "venue": {"id": venue_id, "name": "Yankee Stadium"},
        "dayNight": day_night,
        "teams": {
            "home": {"team": {"id": 147, "abbreviation": abbreviation}},
            "away": {"team": {"id": 111, "abbreviation": "BOS"}},
        },
    }


def test_the_schedule_supplies_the_venue_and_the_day_night_flag():
    record = environment_from_schedule(_game(), batter_is_home=True)
    assert record["venue_id"] == 3313
    assert record["day_night"] == "night"
    assert record["home_team"] == "NYY"
    assert record["batter_is_home"] is True


def test_the_schedule_projection_is_pure():
    """No network: it reads a record `main.fetch_schedule` already holds."""
    assert set(environment_from_schedule(_game())) == ENVIRONMENT_KEYS


@pytest.mark.parametrize("game", [{}, {"venue": {}}, {"teams": {}}])
def test_a_malformed_schedule_record_yields_nones_rather_than_raising(game):
    record = environment_from_schedule(game)
    assert record["venue_id"] is None
    assert record["home_team"] is None


def test_batter_is_home_defaults_to_none_rather_than_false():
    """False would assert the hitter is on the road, which is a claim; None says
    the caller has not resolved it, which is the truth."""
    assert environment_from_schedule(_game())["batter_is_home"] is None


# ---------------------------------------------------------------------------
# fetch_situational_splits
# ---------------------------------------------------------------------------


def _stat_splits_payload(codes, hits=10, pa=50, batters_faced=None):
    splits = []
    for code in codes:
        stat = {"hits": hits, "atBats": pa - 5, "strikeOuts": 8, "baseOnBalls": 4}
        if batters_faced is None:
            stat["plateAppearances"] = pa
        else:
            stat["battersFaced"] = batters_faced
        splits.append({"split": {"code": code}, "stat": stat})
    return {"stats": [{"splits": splits}]}


def test_one_request_covers_all_four_situation_codes(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen["params"] = params
        return FakeResponse(_stat_splits_payload(SITUATIONAL_SIT_CODES))

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch_situational_splits(592450, "hitting", 2026)
    assert seen["params"]["sitCodes"] == "h,a,d,n"
    assert set(result) == set(SITUATIONAL_SIT_CODES)


def test_the_situation_codes_are_home_away_day_night():
    """CALC_37 reads d/n and CALC_38 reads h/a; both come from one response."""
    assert SITUATIONAL_SIT_CODES == ("h", "a", "d", "n")


def test_a_pitchers_batters_faced_arrives_as_plate_appearances(monkeypatch):
    """The pitching group carries no `plateAppearances` key. Without the source
    boundary's substitution CALC_37 and CALC_38 would be None for every pitcher.
    """
    payload = _stat_splits_payload(("h", "a"), hits=58, pa=0, batters_faced=206)
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    result = fetch_situational_splits(668678, "pitching", 2026)
    assert result["h"]["plateAppearances"] == 206


def test_the_hitting_group_keeps_its_own_plate_appearances(monkeypatch):
    payload = _stat_splits_payload(("h",), hits=26, pa=115)
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    result = fetch_situational_splits(592450, "hitting", 2026)
    assert result["h"]["plateAppearances"] == 115


def test_codes_outside_the_requested_set_are_dropped(monkeypatch):
    """A response carrying vl/vr must not leak into a Category 5 lookup."""
    payload = _stat_splits_payload(("h", "vl", "vr"))
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))
    assert set(fetch_situational_splits(592450, "hitting", 2026)) == {"h"}


def test_the_season_defaults_to_the_current_year(monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen["params"] = params
        return FakeResponse(_stat_splits_payload(("h",)))

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_situational_splits(592450, "hitting")
    assert seen["params"]["season"] == date.today().year


def test_a_failing_splits_request_returns_an_empty_mapping(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_situational_splits(592450, "hitting", 2026) == {}


def test_an_unexpected_exception_is_contained(monkeypatch):
    """Matches the fetcher error contract: the calculators then return None."""
    monkeypatch.setattr(
        sources,
        "fetch_stat_splits",
        lambda *a, **k: (_ for _ in ()).throw(ValueError()),
    )
    assert fetch_situational_splits(592450, "hitting", 2026) == {}


# ---------------------------------------------------------------------------
# fetch_batter_spray -- field projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["bb_type", "hc_x", "hc_y", "home_team", "game_type"])
def test_the_spray_and_venue_fields_are_present(field):
    """`hc_x`/`hc_y` are what a spray angle is solved from, `bb_type` restricts it
    to balls hit in the air, and `home_team` is the only thing on a Statcast row
    that says where the game was played."""
    assert field in CATEGORY_05_PITCH_FIELDS


@pytest.mark.parametrize("field", list(CATEGORY_05_PITCH_FIELDS))
def test_every_field_a_calculator_reads_survives_the_projection(field, monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert field in fetch_batter_spray(592450, 2026)[0]


def test_columns_outside_the_field_set_are_dropped(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert "arm_angle" not in fetch_batter_spray(592450, 2026)[0]


def test_one_record_per_pitch(monkeypatch):
    _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(5)))
    assert len(fetch_batter_spray(592450, 2026)) == 5


def test_spring_training_rows_survive_the_fetch(monkeypatch):
    """The fetcher does not filter; excluding non-competitive games is the
    calculators' job."""
    _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame(_rows(1, game_type="S"))
    )
    assert fetch_batter_spray(592450, 2026)[0]["game_type"] == "S"


def test_pandas_missing_sentinels_normalize_to_none(monkeypatch):
    _install_fake_pybaseball(
        monkeypatch,
        frame=_statcast_frame(_rows(1, hc_x=None, hc_y=None, bb_type=None)),
    )
    record = fetch_batter_spray(592450, 2026)[0]
    assert record["hc_x"] is None
    assert record["bb_type"] is None


# ---------------------------------------------------------------------------
# fetch_batter_spray -- side selection, cache sharing, error contract
# ---------------------------------------------------------------------------


def test_the_fetcher_pulls_the_batter_side(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    fetch_batter_spray(592450, 2024)
    assert calls == [("2024-01-01", "2024-12-31", 592450)]


def test_a_second_fetch_of_the_same_batter_and_season_costs_no_second_call(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(2)))
    first = fetch_batter_spray(592450, 2026)
    second = fetch_batter_spray(592450, 2026)
    assert len(calls) == 1
    assert first == second


def test_the_category_8_fetcher_reuses_the_same_cached_frame(monkeypatch):
    """Different field projections, one network call."""
    from calculators.sources.category_08_batter_form import fetch_batter_form

    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    category_05 = fetch_batter_spray(592450, 2026)
    category_08 = fetch_batter_form(592450, 2026)
    assert len(calls) == 1
    assert "hc_x" in category_05[0] and "hc_x" not in category_08[0]


def test_a_pre_statcast_season_returns_empty_without_a_network_call(monkeypatch):
    calls = _install_fake_pybaseball(monkeypatch, frame=_statcast_frame(_rows(1)))
    assert fetch_batter_spray(592450, 2014) == []
    assert calls == []


def test_a_failing_pull_returns_empty_rather_than_raising(monkeypatch):
    _install_fake_pybaseball(monkeypatch, exc=RuntimeError("savant is down"))
    assert fetch_batter_spray(592450, 2026) == []


@pytest.mark.parametrize("frame", [None, "empty"])
def test_an_empty_or_missing_frame_returns_empty(frame, monkeypatch):
    _install_fake_pybaseball(
        monkeypatch, frame=_statcast_frame([]) if frame == "empty" else None
    )
    assert fetch_batter_spray(592450, 2026) == []
