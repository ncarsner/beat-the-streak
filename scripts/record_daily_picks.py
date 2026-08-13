"""Snapshot a day's picks before first pitch so they can be graded afterwards.

This is the first half of a **forward test** of the model against the shipped
`binomial_probability` heuristic, and the first thing in the repo that produces
evidence for issue #35.

Why forward rather than retrospective. A backtest of a past date has to
reconstruct what was knowable on that date, and the season Statcast pull runs
`{season}-01-01` through today: replaying 2026-05-01 for one probe hitter showed
1573 of 2500 pitch records, 62.9%, dated after the replay date. Those are
straightforwardly rewindable with an `end` bound, but Category 1's
`stats=vsPlayer` and the `statSplits` calls return season-to-date aggregates with
no date parameter at all, so part of the model cannot be rewound from the API.
A forward test cannot leak, because the data does not exist yet.

Writes two files per day: `data/picks/YYYY-MM-DD.json`, the durable record
holding every hitter evaluated with both probabilities, and
`data/picks/YYYY-MM-DD.txt`, the rendered table a person reads. The grader reads
only the JSON. Grading is a separate step
(`scripts/grade_daily_picks.py`) so a snapshot is never blocked on outcomes and
the two halves fail independently.

**Tracked in git, deliberately, unlike everything under `.cache/`.** A snapshot
is not a cache: it cannot be regenerated, because tomorrow the season pull
already contains today's games. Losing one loses a day of evidence permanently,
so these accumulate in the repository rather than in a gitignored directory that
a clean checkout or an ephemeral CI runner would discard.

**Run before first pitch.** `select_games` filters to games that have not
started, reading the schedule's own game state rather than comparing against the
scheduled start time, so a hitter whose game is underway is already gone from the
pool and is never recorded as a pick he was ineligible for. The script records
the games it saw and refuses to overwrite a snapshot with a smaller one, since a
later run in the same day sees strictly fewer games.

Usage::

    uv run python3 scripts/record_daily_picks.py [--date YYYY-MM-DD] [--force]

Costs the same as a `--model` run: roughly two Statcast pulls per hitter, cached
on disk, so re-running the same day is cheap.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from prettytable import PrettyTable

# Running this file by path puts `scripts/` on `sys.path`, not the repo root, so
# `import main` raises ModuleNotFoundError. Prepending the repo root makes a bare
# `python3 scripts/record_daily_picks.py` behave like `PYTHONPATH=. python3 ...`,
# which is what the usage line used to require and what a cron entry or a
# copy-pasted command will silently get wrong.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402 - must follow the sys.path bootstrap above

PICKS_DIR = Path(__file__).resolve().parent.parent / "data" / "picks"

# Version the record shape. A grader reading a snapshot written by an older
# version should be able to tell rather than guess, and the forward test is
# expected to run for weeks across code changes.
SNAPSHOT_VERSION = 1


def build_snapshot(date_str: str, now: datetime) -> dict:
    """Evaluate the day's pool and return the snapshot record.

    Mirrors `main.run`'s manual-mode path exactly: same schedule fetch, same
    lineup source, same opener exclusion, same model context. The point of a
    forward test is to grade what the tool actually shows, so any divergence
    here would be measuring a different tool.
    """
    games = main.fetch_schedule(date_str)
    selected = main.select_games(games, now, scheduled=False)

    schedule_map: dict = {}
    all_players: list[dict] = []
    queried_games_cache = main.load_queried_games_cache()
    for g in selected:
        main.process_game_lineup(
            g, queried_games_cache, date_str, schedule_map, all_players
        )

    openers = main.opener_pitcher_ids(selected)
    all_players = main.drop_opener_matchups(all_players, openers)

    model_context = {
        "schedule": main.fetch_hydrated_schedule(date_str),
        "lineups": {g["gamePk"]: main.fetch_lineup(g["gamePk"]) for g in selected},
        "today": now.date(),
        "season": now.year,
    }

    no_data_cache = main.load_no_data_cache()
    summary = main.compile_player_data(
        players=all_players,
        limit=main.MAX_PLAYERS,
        cooldown_days=main.DEFAULT_COOLDOWN_DAYS,
        cache=no_data_cache,
        schedule_map=schedule_map,
        model_context=model_context,
    )
    main.save_no_data_cache(no_data_cache)
    main.save_queried_games_cache(queried_games_cache)

    # `game_pk` is what the grader joins on, so it has to survive the snapshot.
    # `compile_player_data` returns display records, which do not carry it, so
    # it is recovered by player id from the pool that produced them.
    game_pk_by_id = {p["id"]: p.get("game_pk") for p in all_players}
    name_to_id = {p.get("fullName"): p["id"] for p in all_players}

    picks = []
    for row in summary:
        player_id = name_to_id.get(row["Player"])
        picks.append(
            {
                "player_id": player_id,
                "player": row["Player"],
                "team": row["Team"],
                "game_pk": game_pk_by_id.get(player_id),
                "game_hour_utc": row.get("GameHourUTC"),
                "hits_last5": row["Hits"],
                "at_bats_last5": row["At Bats"],
                "walks_last5": row["Walks"],
                "strikeouts_last5": row["Strikeouts"],
                "prob_heuristic": row["probability"],
                "prob_model": row.get("model"),
            }
        )

    return {
        "version": SNAPSHOT_VERSION,
        "date": date_str,
        "recorded_at_utc": now.isoformat(),
        "games_scheduled": len(games),
        "games_upcoming": len(selected),
        "openers_excluded": len(openers),
        "picks": picks,
    }


def snapshot_path(date_str: str) -> Path:
    return PICKS_DIR / f"{date_str}.json"


def rendered_path(date_str: str) -> Path:
    """Path of the human-readable table for *date_str*.

    Written beside the JSON rather than left to stdout: the rendered table is the
    half a person actually reads back weeks later, and a run whose output only
    ever existed in a terminal leaves no trace of what was shown that day.
    """
    return PICKS_DIR / f"{date_str}.txt"


def write_rendered(date_str: str, snapshot: dict, top_n: int) -> tuple[str, Path]:
    """Render *snapshot*, write it to the day's `.txt`, and return both."""
    text = render_snapshot(snapshot, top_n=top_n)
    path = rendered_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    return text, path


def render_snapshot(snapshot: dict, top_n: int = 10) -> str:
    """Return the snapshot as a table.

    The JSON on disk is the durable record the grader reads; this is what a
    person reads. Both methods' rankings are shown side by side rather than one
    combined ordering, because the two disagree about who the best pick is and
    a single ordering would have to pick a winner before anything is validated.
    """
    picks = snapshot["picks"]
    lines = [
        f"Picks recorded for {snapshot['date']} "
        f"({snapshot['games_upcoming']} upcoming games, {len(picks)} hitters)"
    ]
    if snapshot.get("openers_excluded"):
        lines.append(f"{snapshot['openers_excluded']} opener matchup(s) excluded")

    def section(title: str, key: str) -> None:
        ranked = [p for p in picks if p.get(key) is not None]
        if not ranked:
            lines.append(f"\n{title}: no values")
            return
        ranked.sort(key=lambda p: p[key], reverse=True)
        table = PrettyTable()
        table.title = title
        table.field_names = ["#", "Player", "Tm", "H-AB", "BB/K", "Prob %", "Model"]
        table.align["Player"] = "l"
        for i, p in enumerate(ranked[:top_n], 1):
            model = p.get("prob_model")
            table.add_row(
                [
                    i,
                    p["player"],
                    p["team"],
                    f"{p['hits_last5']}-{p['at_bats_last5']}",
                    f"{p['walks_last5']}/{p['strikeouts_last5']}",
                    f"{p['prob_heuristic']:.1%}",
                    f"{model:.1%}" if model is not None else "-",
                ]
            )
        lines.append(str(table))

    section(
        f"Top {top_n} by Prob % (heuristic, what the tool ranks on)", "prob_heuristic"
    )
    section(f"Top {top_n} by Model (unvalidated, #35)", "prob_model")
    return "\n".join(lines)


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing snapshot even if it covers more games",
    )
    parser.add_argument(
        "--top-n", type=int, default=10, help="rows per table, defaults to 10"
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="render an existing snapshot as a table without re-evaluating the slate",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    # Same slate clock as a run, not the host's, so a snapshot taken during a
    # late West Coast game lands in the file for the day those games belong to.
    date_str = args.date or main.slate_date(now).strftime("%Y-%m-%d")
    path = snapshot_path(date_str)

    existing = None
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            existing = None

    # Renders what is already on disk. Has to return before `build_snapshot`,
    # which re-evaluates the whole slate and costs minutes.
    if args.show:
        if existing is None:
            print(f"No readable snapshot at {path}.")
            return
        text, txt_path = write_rendered(date_str, existing, args.top_n)
        print(text)
        print(f"\nWrote {txt_path}")
        return

    snapshot = build_snapshot(date_str, now)

    # A later run sees strictly fewer upcoming games, so silently overwriting
    # would shrink the day's record. Refuse unless asked.
    if existing and not args.force:
        if len(snapshot["picks"]) < len(existing.get("picks", [])):
            print(
                f"Refusing to overwrite {path.name}: existing snapshot has "
                f"{len(existing.get('picks', []))} picks, this run found "
                f"{len(snapshot['picks'])}. Games have started since. Use --force "
                f"to overwrite anyway."
            )
            return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    text, txt_path = write_rendered(date_str, snapshot, args.top_n)

    print(text)
    modelled = sum(1 for p in snapshot["picks"] if p["prob_model"] is not None)
    print(
        f"\nWrote {path}\nWrote {txt_path}\n"
        f"{len(snapshot['picks'])} hitters, {modelled} with a model value"
    )


if __name__ == "__main__":
    main_cli()
