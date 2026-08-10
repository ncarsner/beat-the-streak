"""Tests for the forward-test grader.

The defect these exist to prevent: a Pre-Game boxscore publishes a fully zeroed
batting line for every hitter in a posted lineup, so a grader that trusts the
boxscore alone scores an entire slate as no-hit hours before first pitch and
reports 0% with complete confidence. It is a silent wrong answer, not a crash,
which is the class of bug that survives a smoke test.
"""

import pytest

from scripts import grade_daily_picks as g
from tests.conftest import FakeResponse


def _boxscore(rows, side="home"):
    """rows: [(player_id, name, plate_appearances, hits)]"""
    players = {
        f"ID{pid}": {
            "person": {"id": pid, "fullName": name},
            "stats": {
                "batting": {"plateAppearances": pa, "hits": h} if pa is not None else {}
            },
        }
        for pid, name, pa, h in rows
    }
    other = "away" if side == "home" else "home"
    return {"teams": {side: {"players": players}, other: {"players": {}}}}


def _schedule(games):
    """games: [(gamePk, abstractGameState)]"""
    return {
        "dates": [
            {
                "games": [
                    {"gamePk": pk, "status": {"abstractGameState": state}}
                    for pk, state in games
                ]
            }
        ]
    }


# ---- fetch_final_game_pks ----


def test_only_final_games_are_returned(monkeypatch):
    payload = _schedule([(1, "Final"), (2, "Preview"), (3, "Live"), (4, "Final")])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_final_game_pks("2026-08-10") == {1, 4}


def test_a_slate_with_nothing_final_returns_empty(monkeypatch):
    payload = _schedule([(1, "Preview"), (2, "Preview")])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_final_game_pks("2026-08-10") == set()


def test_schedule_failure_returns_empty_rather_than_raising(monkeypatch):
    def boom(*a, **kw):
        raise g.requests.RequestException("down")

    monkeypatch.setattr(g.requests, "get", boom)
    assert g.fetch_final_game_pks("2026-08-10") == set()


# ---- fetch_game_outcomes ----


def test_a_hitter_with_a_hit_grades_true(monkeypatch):
    payload = _boxscore([(1, "A", 4, 2)])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_game_outcomes(700) == {1: True}


def test_a_hitless_hitter_grades_false(monkeypatch):
    payload = _boxscore([(1, "A", 4, 0)])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_game_outcomes(700) == {1: False}


def test_a_hitter_who_only_walked_still_counts_as_having_played(monkeypatch):
    """A walk is a plate appearance with no at-bat. He played and did not hit."""
    payload = _boxscore([(1, "A", 1, 0)])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_game_outcomes(700) == {1: False}


def test_a_hitter_who_never_batted_is_omitted(monkeypatch):
    """Zero plate appearances is an absent prediction, not a failed one."""
    payload = _boxscore([(1, "Played", 4, 1), (2, "Benched", 0, 0)])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_game_outcomes(700) == {1: True}


def test_a_player_with_no_batting_stats_is_omitted(monkeypatch):
    payload = _boxscore([(1, "Pitcher", None, None), (2, "Hitter", 3, 1)])
    monkeypatch.setattr(g.requests, "get", lambda *a, **kw: FakeResponse(payload))
    assert g.fetch_game_outcomes(700) == {2: True}


def test_boxscore_failure_returns_none(monkeypatch):
    def boom(*a, **kw):
        raise g.requests.RequestException("down")

    monkeypatch.setattr(g.requests, "get", boom)
    assert g.fetch_game_outcomes(700) is None


# ---- grade_snapshot: the pre-game regression ----


def _snapshot(picks, date="2026-08-10"):
    return {"version": 1, "date": date, "picks": picks}


def _pick(player_id, game_pk=700, heuristic=0.8, model=0.6):
    return {
        "player_id": player_id,
        "player": f"P{player_id}",
        "team": "TST",
        "game_pk": game_pk,
        "prob_heuristic": heuristic,
        "prob_model": model,
    }


def test_a_pregame_slate_grades_nothing(monkeypatch):
    """The regression. A Preview boxscore carries zeroed lines for every hitter;
    grading them would report a confident 0% hit rate before first pitch."""
    calls = []

    def fake_get(url, *a, **kw):
        if "/schedule" in url:
            return FakeResponse(_schedule([(700, "Preview")]))
        calls.append(url)
        return FakeResponse(_boxscore([(1, "A", 0, 0), (2, "B", 0, 0)]))

    monkeypatch.setattr(g.requests, "get", fake_get)
    assert g.grade_snapshot(_snapshot([_pick(1), _pick(2)])) is None
    assert calls == [], "must not fetch a boxscore for an unfinished game"


def test_a_final_slate_grades_its_hitters(monkeypatch):
    def fake_get(url, *a, **kw):
        if "/schedule" in url:
            return FakeResponse(_schedule([(700, "Final")]))
        return FakeResponse(_boxscore([(1, "A", 4, 1), (2, "B", 4, 0)]))

    monkeypatch.setattr(g.requests, "get", fake_get)
    result = g.grade_snapshot(_snapshot([_pick(1), _pick(2)]))
    assert [p["got_hit"] for p in result["graded"]] == [True, False]


def test_a_partially_final_day_grades_only_the_finished_games(monkeypatch):
    """An afternoon game is over while a night game is not. Legitimate, and the
    pending game must contribute nothing rather than counting as no-hit."""

    def fake_get(url, *a, **kw):
        if "/schedule" in url:
            return FakeResponse(_schedule([(700, "Final"), (701, "Live")]))
        if "/701/" in url:
            pytest.fail("fetched a boxscore for a game that is not final")
        return FakeResponse(_boxscore([(1, "A", 4, 1)]))

    monkeypatch.setattr(g.requests, "get", fake_get)
    result = g.grade_snapshot(_snapshot([_pick(1, game_pk=700), _pick(2, game_pk=701)]))
    assert len(result["graded"]) == 1
    assert result["graded"][0]["player_id"] == 1


def test_a_scratched_hitter_is_dropped_not_counted_as_a_miss(monkeypatch):
    def fake_get(url, *a, **kw):
        if "/schedule" in url:
            return FakeResponse(_schedule([(700, "Final")]))
        return FakeResponse(_boxscore([(1, "A", 4, 1)]))

    monkeypatch.setattr(g.requests, "get", fake_get)
    result = g.grade_snapshot(_snapshot([_pick(1), _pick(99)]))
    assert len(result["graded"]) == 1


# ---- scoring ----


@pytest.mark.parametrize(
    "prob, outcome, expected",
    [(1.0, True, 0.0), (0.0, False, 0.0), (0.0, True, 1.0), (0.5, True, 0.25)],
)
def test_brier(prob, outcome, expected):
    assert g._brier(prob, outcome) == pytest.approx(expected)


def test_log_loss_clamps_a_confident_wrong_answer(monkeypatch):
    """Unclamped this is infinity, which would destroy the whole average."""
    assert g._log_loss(0.0, True) < 20
    assert g._log_loss(1.0, False) < 20


def test_log_loss_rewards_a_confident_right_answer():
    assert g._log_loss(0.99, True) < g._log_loss(0.51, True)


def test_summarize_reports_both_methods(capsys):
    days = [
        {
            "date": "2026-08-10",
            "graded": [
                {**_pick(1, heuristic=0.9, model=0.6), "got_hit": True},
                {**_pick(2, heuristic=0.2, model=0.7), "got_hit": False},
            ],
        }
    ]
    g.summarize(days, top_n=2)
    out = capsys.readouterr().out
    assert "heuristic" in out
    assert "model" in out
    assert "Graded days: 1" in out


def test_summarize_warns_below_the_signal_threshold(capsys):
    days = [
        {
            "date": "2026-08-10",
            "graded": [{**_pick(1), "got_hit": True}],
        }
    ]
    g.summarize(days, top_n=1)
    assert "not meaningful" in capsys.readouterr().out


def test_top1_follows_each_methods_own_ranking(capsys):
    """The two methods disagree about who the best pick is, which is the whole
    point of the comparison. The heuristic's favourite missed, the model's hit."""
    days = [
        {
            "date": "2026-08-10",
            "graded": [
                {**_pick(1, heuristic=0.9, model=0.5), "got_hit": False},
                {**_pick(2, heuristic=0.2, model=0.9), "got_hit": True},
            ],
        }
    ]
    g.summarize(days, top_n=1)
    lines = capsys.readouterr().out.splitlines()
    heuristic = next(ln for ln in lines if ln.startswith("heuristic"))
    model = next(ln for ln in lines if ln.startswith("model"))
    assert "0.0%" in heuristic
    assert "100.0%" in model


def test_a_pick_with_no_model_value_is_skipped_by_the_model_only(capsys):
    days = [
        {
            "date": "2026-08-10",
            "graded": [
                {**_pick(1, heuristic=0.9, model=None), "got_hit": True},
                {**_pick(2, heuristic=0.2, model=0.9), "got_hit": True},
            ],
        }
    ]
    g.summarize(days, top_n=5)
    out = capsys.readouterr().out
    heuristic = next(ln for ln in out.splitlines() if ln.startswith("heuristic"))
    model = next(ln for ln in out.splitlines() if ln.startswith("model"))
    assert heuristic.split()[-1] == "2"
    assert model.split()[-1] == "1"
