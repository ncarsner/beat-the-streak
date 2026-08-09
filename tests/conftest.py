"""Shared test helpers used across multiple test modules.

Plain functions (not fixtures) so test bodies can call them directly without
changing function signatures. Importable as ``from tests.conftest import ...``.

Also installs the autouse network guard below, which enforces the suite's
central invariant: no test ever reaches the live API.
"""

import itertools
import socket

import pytest
import requests


# Loopback stays reachable so a future test can talk to a local fixture server.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class LiveNetworkAttempted(BaseException):
    """Raised when a test tries to open a real connection.

    Derives from `BaseException`, not `Exception`, and that is the whole point.
    `fetch_bvp_statcast` wraps its pybaseball call in a deliberately broad
    `except Exception` because the library's failure modes are undocumented — so
    an `Exception` here gets swallowed, the fetcher returns `[]`, and the leaking
    test passes green while the guard silently does nothing. Verified: the first
    version of this guard raised `RuntimeError` and the leak it was written to
    catch still passed. pytest reports a `BaseException` as a failure, so this
    propagates through every `except Exception` in the codebase.
    """


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Fail any test that opens a real network connection.

    The suite is offline by design: every fetcher is monkeypatched and every
    payload is a fixture. That invariant was stated in the docs long before
    anything enforced it, and it silently broke twice — both times because a
    test patched one of the two fetchers `attach_category_01` calls and left the
    other live. Neither failed. Neither was even red; the only symptom was a test
    that took 0.43s in a suite where nothing else exceeded 0.05s.

    Blocking at the socket layer rather than at `requests` is deliberate: the
    calls that escaped came from pybaseball, which does its own HTTP and never
    touches a `requests` symbol a test thought to patch. A connection is the one
    thing every escape route has in common.
    """

    def _blocked(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in _ALLOWED_HOSTS:
            return _real_connect(self, address, *args, **kwargs)
        raise LiveNetworkAttempted(
            f"Test attempted a live network connection to {host!r}. The suite "
            "must never reach the live API — patch the fetcher this code path "
            "calls. Note that attach_category_01 calls two of them: "
            "fetch_bvp_stats and fetch_bvp_statcast."
        )

    def _blocked_ex(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in _ALLOWED_HOSTS:
            return _real_connect_ex(self, address, *args, **kwargs)
        raise LiveNetworkAttempted(
            f"Test attempted a live network connection to {host!r}."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_ex)


@pytest.fixture(autouse=True)
def _redirect_statcast_cache(tmp_path, monkeypatch):
    """Redirect the Statcast cache to a per-test temp directory.

    Without this, a real .cache/statcast/ file on disk can satisfy a cache
    lookup inside fetch_bvp_statcast, causing the test to skip the stubbed
    pybaseball call and silently pass or fail for the wrong reason.
    """
    import calculators.sources.common as _sources_common

    monkeypatch.setattr(_sources_common, "CACHE_DIR", tmp_path / "statcast")


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


# ---- BvP payload builders (shared by category_01 calculator and source tests) ----


def _split(season=None, pa=0, ab=0, h=0, so=0, bb=0):
    stat = {
        "plateAppearances": pa,
        "atBats": ab,
        "hits": h,
        "strikeOuts": so,
        "baseOnBalls": bb,
    }
    return {"season": season, "stat": stat}


def _vsplayer_payload(season_splits, total=None):
    """Build a raw stats=vsPlayer response with optional vsPlayerTotal group."""
    stats = [{"type": {"displayName": "vsPlayer"}, "splits": season_splits}]
    if total is not None:
        stats.append({"type": {"displayName": "vsPlayerTotal"}, "splits": [total]})
    return {"stats": stats}


# ---- Statcast pitch-record builder (shared by category_01 and source tests) ----


def _pitch(description="ball", events=None, strikes=0, ev=None, xba=None, xwoba=None):
    return {
        "description": description,
        "events": events,
        "strikes": strikes,
        "launch_speed": ev,
        "estimated_ba_using_speedangle": xba,
        "estimated_woba_using_speedangle": xwoba,
    }


# ---- Category 3 arsenal pitch builder (calculator and source tests) ----
#
# Two real Statcast trajectory rows, lifted from the same 2026 league sample the
# tier boundaries were measured against. Tests that need a *known* vertical
# approach angle use these rather than invented numbers: hand-picked vy0/ay/vz0/az
# values can easily describe a pitch that could not physically be thrown, and the
# VAA solution would then be exercised on geometry it will never see.
REAL_FOUR_SEAMER = {
    "vy0": -131.018533,
    "ay": 24.137732,
    "vz0": -4.933477,
    "az": -20.932360,
}  # solves to -6.086148 degrees; release_speed 90.3

REAL_CURVEBALL = {
    "vy0": -110.243528,
    "ay": 21.474273,
    "vz0": -3.332133,
    "az": -36.793284,
}  # solves to -11.443100 degrees; release_speed 75.8


def _arsenal_pitch(
    game_pk=1,
    at_bat_number=1,
    pitch_number=1,
    events=None,
    pitch_type="FF",
    release_speed=93.0,
    effective_speed=None,
    release_extension=6.4,
    delta_run_exp=None,
    pfx_x=None,
    xba=None,
    attack_angle=None,
    trajectory=None,
):
    """Build one Category 3 pitch record.

    *trajectory* is a dict of vy0/ay/vz0/az — pass `REAL_FOUR_SEAMER` or
    `REAL_CURVEBALL` when the vertical approach angle matters, and leave it None
    when it does not, which omits the four keys entirely so
    `vertical_approach_angle` returns None the way a pre-tracking row would.
    """
    record = {
        "game_date": "2026-07-01",
        "game_pk": game_pk,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
        "events": events,
        "pitch_type": pitch_type,
        "release_speed": release_speed,
        "effective_speed": effective_speed,
        "release_extension": release_extension,
        "delta_run_exp": delta_run_exp,
        "pfx_x": pfx_x,
        "estimated_ba_using_speedangle": xba,
        "attack_angle": attack_angle,
    }
    if trajectory:
        record.update(trajectory)
    return record


def _discipline_pitch(
    game_pk=1,
    at_bat_number=1,
    pitch_number=1,
    events=None,
    description="ball",
    pitch_type=None,
    zone=5,
    balls=0,
    strikes=0,
    xba=None,
):
    """Build one Category 4 pitch record.

    *zone* defaults to 5, the middle of the strike zone, so a test that does not
    care about location still lands somewhere valid. Pass ``zone=None`` for an
    untracked pitch, which is what a pitch-timer violation looks like in the
    source data.

    *pitch_type* is unused by the Category 4 field tuple and defaults to None; it
    exists so a test can hand the same record to a Category 3 helper when
    checking that the two categories agree on a shared reduction.
    """
    record = {
        "game_date": "2026-07-01",
        "game_pk": game_pk,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
        "events": events,
        "description": description,
        "type": _PITCH_TYPE_CODE.get(description, "B"),
        "zone": zone,
        "balls": balls,
        "strikes": strikes,
        "estimated_ba_using_speedangle": xba,
    }
    if pitch_type is not None:
        record["pitch_type"] = pitch_type
    return record


# Statcast's `type` trichotomy, derived from `description` so a fixture cannot
# describe a called strike typed as a ball. Verified against the probe frames:
# every description maps to exactly one type, with `hit_into_play` the only X.
_PITCH_TYPE_CODE = {
    "ball": "B",
    "blocked_ball": "B",
    "automatic_ball": "B",
    "hit_by_pitch": "B",
    "pitchout": "B",
    "called_strike": "S",
    "foul": "S",
    "foul_tip": "S",
    "foul_bunt": "S",
    "bunt_foul_tip": "S",
    "swinging_strike": "S",
    "swinging_strike_blocked": "S",
    "missed_bunt": "S",
    "hit_into_play": "X",
}


def _form_pitch(
    game_date="2026-07-01",
    game_pk=1,
    game_type="R",
    at_bat_number=1,
    pitch_number=1,
    events=None,
    description="ball",
    launch_speed=None,
    launch_angle=None,
    xba=None,
    xwoba=None,
):
    """Build one Category 8 pitch record.

    Defaults to a competitive regular-season take, so a test that cares only
    about dates or events does not have to restate the rest. Pass
    ``game_type="S"`` for a spring-training row and ``description="hit_into_play"``
    with a launch reading for a batted ball.
    """
    return {
        "game_date": game_date,
        "game_pk": game_pk,
        "game_type": game_type,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
        "events": events,
        "description": description,
        "launch_speed": launch_speed,
        "launch_angle": launch_angle,
        "estimated_ba_using_speedangle": xba,
        "estimated_woba_using_speedangle": xwoba,
    }


def _form_pa(events=None, *, hit=False, **kwargs):
    """One Category 8 plate appearance, expressed as its single terminal pitch.

    ``hit=True`` is shorthand for a single put in play, the common case in a
    hit-rate test. Every Category 8 calculator reduces to terminal pitches
    itself, so a one-pitch plate appearance is a faithful fixture.
    """
    if hit and events is None:
        events = "single"
    if events in {"single", "double", "triple", "home_run"}:
        kwargs.setdefault("description", "hit_into_play")
    return _form_pitch(events=events, **kwargs)


def _form_pitch_thrown(
    game_date="2026-07-01",
    game_pk=1,
    game_type="R",
    at_bat_number=1,
    pitch_number=1,
    inning=1,
    inning_topbot="Top",
    outs_when_up=0,
    events=None,
    description="ball",
    pitch_type="FF",
    release_speed=94.0,
    zone=5,
    launch_speed=None,
    bat_score=0,
    post_bat_score=0,
):
    """Build one Category 9 pitch record.

    Defaults to the first pitch of a start: inning 1, zero outs, a competitive
    regular-season game. `_start` below composes these into whole outings.
    """
    return {
        "game_date": game_date,
        "game_pk": game_pk,
        "game_type": game_type,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
        "inning": inning,
        "inning_topbot": inning_topbot,
        "outs_when_up": outs_when_up,
        "events": events,
        "description": description,
        "pitch_type": pitch_type,
        "release_speed": release_speed,
        "zone": zone,
        "launch_speed": launch_speed,
        "bat_score": bat_score,
        "post_bat_score": post_bat_score,
    }


def _start(game_pk=1, game_date="2026-07-01", innings=1, events_per_inning=None, **kw):
    """Compose a whole start as one-pitch plate appearances.

    *events_per_inning* is a list of event lists, one per inning; it defaults to
    three routine outs per inning. `outs_when_up` is advanced within each inning
    the way the real feed does, so the outs reconstruction sees a faithful shape.

    Any extra keyword is passed through to every pitch, which is how a test sets
    a velocity, a zone, or a game type across a whole start at once.
    """
    if events_per_inning is None:
        events_per_inning = [["field_out"] * 3 for _ in range(innings)]
    pitches = []
    ab = 1
    for offset, events in enumerate(events_per_inning):
        outs = 0
        for event in events:
            pitches.append(
                _form_pitch_thrown(
                    game_pk=game_pk,
                    game_date=game_date,
                    at_bat_number=ab,
                    inning=offset + 1,
                    outs_when_up=outs,
                    events=event,
                    **kw,
                )
            )
            ab += 1
            outs += _OUTS.get(event, 0)
    return pitches


_OUTS = {
    "field_out": 1,
    "strikeout": 1,
    "force_out": 1,
    "sac_fly": 1,
    "grounded_into_double_play": 2,
    "double_play": 2,
    "triple_play": 3,
}


# ---- Category 5 ballpark / environment builders ----


def _hc_for_angle(angle_degrees, radius=150.0):
    """``(hc_x, hc_y)`` for a batted ball at *angle_degrees* off dead centre.

    The inverse of `spray_angle`, so a fixture can name the sector it means
    instead of hand-picking coordinates whose angle has to be recomputed by the
    reader. Negative is left field. *radius* only has to be non-zero; the angle
    is scale-free.
    """
    import math

    from calculators.category_05_ballpark_environment import HOME_PLATE_HC

    origin_x, origin_y = HOME_PLATE_HC
    radians = math.radians(angle_degrees)
    return origin_x + radius * math.sin(radians), origin_y - radius * math.cos(radians)


def _env_pitch(
    game_date="2026-07-01",
    game_pk=1,
    game_type="R",
    at_bat_number=1,
    pitch_number=1,
    events=None,
    description="ball",
    bb_type=None,
    angle=None,
    hc_x=None,
    hc_y=None,
    home_team="NYY",
    away_team="BOS",
):
    """Build one Category 5 pitch record.

    Pass *angle* to place a batted ball in a named sector; the coordinates are
    solved from it. Pass *hc_x* / *hc_y* directly only when testing the
    coordinate handling itself, including the untracked case where both are None.
    """
    if angle is not None:
        hc_x, hc_y = _hc_for_angle(angle)
    return {
        "game_date": game_date,
        "game_pk": game_pk,
        "game_type": game_type,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
        "events": events,
        "description": description,
        "bb_type": bb_type,
        "hc_x": hc_x,
        "hc_y": hc_y,
        "home_team": home_team,
        "away_team": away_team,
    }


_air_ball_counter = itertools.count(1)


def _air_ball(angle, events="field_out", **kwargs):
    """A batted ball hit into the air at *angle*, the fixture `CALC_33` needs.

    Each call gets a fresh `at_bat_number` from a module-level counter unless the
    caller names one. A counter rather than a hash of the arguments: `hash` is
    salted per interpreter run for anything containing a string, so plate
    appearance identity would be nondeterministic across runs and two fixtures
    could collide on one run and not the next.
    """
    kwargs.setdefault("bb_type", "fly_ball")
    kwargs.setdefault("at_bat_number", next(_air_ball_counter))
    return _env_pitch(angle=angle, description="hit_into_play", events=events, **kwargs)


def _ballpark(fences=(330, 385, 405, 385, 330), roof="Open", elevation=100):
    return {
        "name": "Test Park",
        "elevation_ft": elevation,
        "azimuth_degrees": 90.0,
        "roof_type": roof,
        "turf_type": "Grass",
        "fences_ft": list(fences),
    }


def _park_factor(all_index=100, left=100, right=100, closed=None, n_pa=50000):
    """A one-venue park-factor table entry.

    *closed* is ``(index, n_pa)`` for the roof-closed grouping, omitted entirely
    when None so an open-air park has no such grouping, matching the real table.
    """
    groupings = {
        "All": {"n_pa": n_pa, "index_hits": all_index},
        "L": {"n_pa": n_pa // 2, "index_hits": left},
        "R": {"n_pa": n_pa // 2, "index_hits": right},
    }
    if closed is not None:
        closed_index, closed_pa = closed
        groupings["roof_closed"] = {"n_pa": closed_pa, "index_hits": closed_index}
    return {"name": "Test Park", "groupings": groupings}


# ---- Category 6 lineup / times-through-order builders ----


def _lineup_line(obp=".340", slg=".420", ops=".760", pa=400):
    """One hitter's season rate line as the MLB Stats API serves it.

    The three rates are **strings** on purpose: the API sends ".375", and a
    fixture that hands over a float would not exercise the parse that every real
    response goes through.
    """
    return {"obp": obp, "slg": slg, "ops": ops, "plateAppearances": pa}


def _tto_pa(
    game_pk=1,
    at_bat_number=1,
    pitch_number=1,
    inning=1,
    outs_when_up=0,
    events="field_out",
    batter=100,
    pitcher=200,
    game_type="R",
    game_date="2026-07-01",
):
    """One Category 6 plate appearance, expressed as its terminal pitch.

    Carries both the pitcher-side fields (`outs_when_up`, `batter`) and the
    batter-side ones (`pitcher`), so a single fixture can be handed to either
    reconstruction and the two can be compared over the same rows.
    """
    return {
        "game_date": game_date,
        "game_pk": game_pk,
        "game_type": game_type,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
        "inning": inning,
        "outs_when_up": outs_when_up,
        "events": events,
        "batter": batter,
        "pitcher": pitcher,
    }


def _start_pas(game_pk=1, batters=(101, 102, 103), turns=3, events="field_out", **kw):
    """A start as one-pitch plate appearances, *turns* times through *batters*.

    The first plate appearance is inning 1 with nobody out, so the start test
    recognises it. `at_bat_number` increases monotonically, which is the order
    both reconstructions walk.
    """
    pas = []
    number = 1
    for turn in range(turns):
        for batter in batters:
            pas.append(
                _tto_pa(
                    game_pk=game_pk,
                    at_bat_number=number,
                    inning=turn + 1,
                    outs_when_up=0 if number == 1 else 1,
                    events=events,
                    batter=batter,
                    **kw,
                )
            )
            number += 1
    return pas


# ---- Category 10 schedule builders ----


def _sched_game(
    game_pk=1,
    game_date="2026-08-03",
    game_number=1,
    day_night="night",
    venue_id=3313,
    start_hour=23,
):
    """One record as `fetch_team_game_log` normalizes a schedule game.

    `start_time` is timezone-aware UTC, matching what the fetcher parses out of
    the API's ISO-8601 `gameDate`.
    """
    from datetime import datetime, timezone

    try:
        day = datetime.fromisoformat(game_date)
        start = datetime(day.year, day.month, day.day, start_hour, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        # Mirrors the fetcher, whose `_parse_start` returns None on a value it
        # cannot parse rather than raising.
        start = None
    return {
        "game_pk": game_pk,
        "game_date": game_date,
        "game_number": game_number,
        "day_night": day_night,
        "venue_id": venue_id,
        "start_time": start,
    }


# Two real venues, used where a distance or a time-zone shift has to be a number
# someone can check. Wrigley Field to Yankee Stadium is 715.1 miles great-circle
# and one hour eastward during daylight saving.
WRIGLEY = {
    "name": "Wrigley Field",
    "latitude": 41.9484,
    "longitude": -87.6553,
    "timezone_id": "America/Chicago",
    "utc_offset_hours": -5,
    "elevation_ft": 595,
    "roof_type": "Open",
    "fences_ft": [355, 368, 400, 368, 353],
}

YANKEE = {
    "name": "Yankee Stadium",
    "latitude": 40.82919482,
    "longitude": -73.9264977,
    "timezone_id": "America/New_York",
    "utc_offset_hours": -4,
    "elevation_ft": 55,
    "roof_type": "Open",
    "fences_ft": [318, 399, 408, 385, 314],
}

PHOENIX = {
    "name": "Chase Field",
    "latitude": 33.4455,
    "longitude": -112.0667,
    "timezone_id": "America/Phoenix",
    "utc_offset_hours": -7,
    "elevation_ft": 1086,
    "roof_type": "Retractable",
    "fences_ft": [328, 412, 407, 414, 335],
}

SAN_FRANCISCO = {
    "name": "Oracle Park",
    "latitude": 37.7786,
    "longitude": -122.3893,
    "timezone_id": "America/Los_Angeles",
    "utc_offset_hours": -7,
    "elevation_ft": 0,
    "roof_type": "Open",
    "fences_ft": [339, 399, 391, 415, 309],
}

TEST_BALLPARKS = {
    "17": WRIGLEY,
    "3313": YANKEE,
    "15": PHOENIX,
    "2395": SAN_FRANCISCO,
}


# ---- pybaseball stub (shared by source tests) ----


def _install_fake_pybaseball(monkeypatch, frame=None, exc=None, pitcher_frame=None):
    """Stub the pybaseball import inside the Statcast fetchers.

    Stubs both entry points. `statcast_pitcher` serves *pitcher_frame* when given
    and falls back to *frame*, so a test that does not care which side it is on
    can pass one frame and a test comparing the two sides can pass both. Recorded
    calls carry the entry point name so a test can assert which side was pulled —
    the batter and pitcher fetchers share a cache namespace per role, and getting
    that wrong would silently serve one side's frame to the other.
    """
    import sys
    import types

    calls = []

    def statcast_pitcher(start_dt, end_dt, player_id):
        calls.append(("pitcher", start_dt, end_dt, player_id))
        if exc is not None:
            raise exc
        return frame if pitcher_frame is None else pitcher_frame

    def statcast_batter(start_dt, end_dt, player_id):
        calls.append((start_dt, end_dt, player_id))
        if exc is not None:
            raise exc
        return frame

    module = types.ModuleType("pybaseball")
    module.statcast_batter = statcast_batter
    module.statcast_pitcher = statcast_pitcher
    monkeypatch.setitem(sys.modules, "pybaseball", module)
    return calls


def _statcast_frame(rows):
    import pandas as pd

    return pd.DataFrame(rows)
