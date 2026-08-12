"""Tests for the snapshot recorder's rendering and file-writing halves.

`build_snapshot` is deliberately not tested here: it is a thin orchestration of
`main` functions that each have their own tests, and exercising it would mean
mocking an entire slate. What is tested is everything that decides what lands on
disk, which is the part that failed silently before (a run rendered to stdout and
left no file behind).
"""

import json

import pytest

from scripts import record_daily_picks as rec


def _pick(name: str, team: str, heuristic: float, model: float | None) -> dict:
    return {
        "player_id": 1,
        "player": name,
        "team": team,
        "game_pk": 700001,
        "game_hour_utc": 23,
        "hits_last5": 5,
        "at_bats_last5": 18,
        "walks_last5": 2,
        "strikeouts_last5": 3,
        "prob_heuristic": heuristic,
        "prob_model": model,
    }


@pytest.fixture
def snapshot() -> dict:
    return {
        "version": rec.SNAPSHOT_VERSION,
        "date": "2026-08-12",
        "recorded_at_utc": "2026-08-12T21:00:00+00:00",
        "games_scheduled": 15,
        "games_upcoming": 7,
        "openers_excluded": 1,
        "picks": [
            _pick("Alpha Batter", "TOR", 0.81, 0.67),
            _pick("Beta Batter", "BOS", 0.42, 0.71),
            _pick("Gamma Batter", "STL", 0.55, None),
        ],
    }


@pytest.fixture
def picks_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "PICKS_DIR", tmp_path / "picks")
    return tmp_path / "picks"


def test_rendered_path_sits_beside_the_snapshot(picks_dir):
    assert (
        rec.rendered_path("2026-08-12").parent == rec.snapshot_path("2026-08-12").parent
    )
    assert rec.rendered_path("2026-08-12").name == "2026-08-12.txt"


def test_write_rendered_writes_the_text_it_returns(picks_dir, snapshot):
    text, path = rec.write_rendered("2026-08-12", snapshot, top_n=10)
    assert path == picks_dir / "2026-08-12.txt"
    assert path.read_text() == text + "\n"


def test_write_rendered_creates_a_missing_directory(picks_dir, snapshot):
    assert not picks_dir.exists()
    _, path = rec.write_rendered("2026-08-12", snapshot, top_n=10)
    assert path.exists()


def test_rendered_file_holds_both_rankings(picks_dir, snapshot):
    text, _ = rec.write_rendered("2026-08-12", snapshot, top_n=10)
    assert "7 upcoming games, 3 hitters" in text
    assert "1 opener matchup(s) excluded" in text
    assert "by Prob %" in text
    assert "by Model" in text
    # Ranked by model, so Beta (.71) leads Alpha (.67); the hitter with no model
    # value is absent from that table rather than ranked as a zero.
    model_table = text.split("by Model")[1]
    assert model_table.index("Beta Batter") < model_table.index("Alpha Batter")
    assert "Gamma Batter" not in model_table


def test_rendered_file_respects_top_n(picks_dir, snapshot):
    text, _ = rec.write_rendered("2026-08-12", snapshot, top_n=1)
    heuristic_table = text.split("by Model")[0]
    assert "Alpha Batter" in heuristic_table
    assert "Beta Batter" not in heuristic_table


def test_show_writes_the_text_file_from_an_existing_snapshot(
    picks_dir, snapshot, monkeypatch, capsys
):
    """The gap this closes: a snapshot on disk with no rendered file beside it.

    `--show` re-derives the table from the JSON, so a day whose run printed to a
    terminal and nowhere else can be given its `.txt` without re-evaluating the
    slate.
    """
    picks_dir.mkdir(parents=True)
    (picks_dir / "2026-08-12.json").write_text(json.dumps(snapshot))
    monkeypatch.setattr(
        "sys.argv",
        ["record_daily_picks.py", "--date", "2026-08-12", "--show"],
    )

    rec.main_cli()

    txt = picks_dir / "2026-08-12.txt"
    assert txt.exists()
    assert "Alpha Batter" in txt.read_text()
    assert "Wrote" in capsys.readouterr().out


def test_show_without_a_snapshot_writes_nothing(picks_dir, monkeypatch, capsys):
    picks_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "sys.argv",
        ["record_daily_picks.py", "--date", "2026-08-12", "--show"],
    )

    rec.main_cli()

    assert not (picks_dir / "2026-08-12.txt").exists()
    assert "No readable snapshot" in capsys.readouterr().out


def test_recording_writes_both_files(picks_dir, snapshot, monkeypatch, capsys):
    """Drives `main_cli` rather than the writers directly, because the defect
    being guarded against was a `main_cli` path that skipped one of them."""
    monkeypatch.setattr(rec, "build_snapshot", lambda date_str, now: snapshot)
    monkeypatch.setattr("sys.argv", ["record_daily_picks.py", "--date", "2026-08-12"])

    rec.main_cli()

    written = json.loads((picks_dir / "2026-08-12.json").read_text())
    assert written["picks"] == snapshot["picks"]
    assert "Alpha Batter" in (picks_dir / "2026-08-12.txt").read_text()

    out = capsys.readouterr().out
    assert "3 hitters, 2 with a model value" in out


def test_refusing_to_shrink_a_snapshot_writes_neither_file(
    picks_dir, snapshot, monkeypatch, capsys
):
    """A later run sees fewer games. It must not overwrite the JSON, and it must
    not overwrite the rendered file either: a `.txt` narrower than the `.json`
    beside it would misreport the day."""
    picks_dir.mkdir(parents=True)
    (picks_dir / "2026-08-12.json").write_text(json.dumps(snapshot))
    (picks_dir / "2026-08-12.txt").write_text("original render\n")

    shrunk = dict(snapshot, picks=snapshot["picks"][:1])
    monkeypatch.setattr(rec, "build_snapshot", lambda date_str, now: shrunk)
    monkeypatch.setattr("sys.argv", ["record_daily_picks.py", "--date", "2026-08-12"])

    rec.main_cli()

    assert len(json.loads((picks_dir / "2026-08-12.json").read_text())["picks"]) == 3
    assert (picks_dir / "2026-08-12.txt").read_text() == "original render\n"
    assert "Refusing to overwrite" in capsys.readouterr().out


def test_force_overwrites_both_files(picks_dir, snapshot, monkeypatch):
    picks_dir.mkdir(parents=True)
    (picks_dir / "2026-08-12.json").write_text(json.dumps(snapshot))
    (picks_dir / "2026-08-12.txt").write_text("original render\n")

    shrunk = dict(snapshot, picks=snapshot["picks"][:1])
    monkeypatch.setattr(rec, "build_snapshot", lambda date_str, now: shrunk)
    monkeypatch.setattr(
        "sys.argv", ["record_daily_picks.py", "--date", "2026-08-12", "--force"]
    )

    rec.main_cli()

    assert len(json.loads((picks_dir / "2026-08-12.json").read_text())["picks"]) == 1
    assert (picks_dir / "2026-08-12.txt").read_text() != "original render\n"


def test_unreadable_existing_snapshot_does_not_block_a_write(
    picks_dir, snapshot, monkeypatch
):
    picks_dir.mkdir(parents=True)
    (picks_dir / "2026-08-12.json").write_text("{ not json")
    monkeypatch.setattr(rec, "build_snapshot", lambda date_str, now: snapshot)
    monkeypatch.setattr("sys.argv", ["record_daily_picks.py", "--date", "2026-08-12"])

    rec.main_cli()

    assert len(json.loads((picks_dir / "2026-08-12.json").read_text())["picks"]) == 3
    assert (picks_dir / "2026-08-12.txt").exists()


def test_render_reports_no_values_when_the_model_is_absent(picks_dir):
    snapshot = {
        "date": "2026-08-12",
        "games_upcoming": 1,
        "openers_excluded": 0,
        "picks": [_pick("Alpha Batter", "TOR", 0.81, None)],
    }
    text, _ = rec.write_rendered("2026-08-12", snapshot, top_n=10)
    assert "by Model (unvalidated, #35): no values" in text
