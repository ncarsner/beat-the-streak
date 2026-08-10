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

Writes one JSON record per day to `data/picks/YYYY-MM-DD.json`, holding every
hitter evaluated with both probabilities. Grading is a separate step
(`scripts/grade_daily_picks.py`) so a snapshot is never blocked on outcomes and
the two halves fail independently.

**Tracked in git, deliberately, unlike everything under `.cache/`.** A snapshot
is not a cache: it cannot be regenerated, because tomorrow the season pull
already contains today's games. Losing one loses a day of evidence permanently,
so these accumulate in the repository rather than in a gitignored directory that
a clean checkout or an ephemeral CI runner would discard.

**Run before first pitch.** `select_games` filters to games that have not
started, so a hitter whose game is underway is already gone from the pool. The
script records the games it saw and refuses to overwrite a snapshot with a
smaller one, since a later run in the same day sees strictly fewer games.

Usage::

    PYTHONPATH=. python3 scripts/record_daily_picks.py [--date YYYY-MM-DD] [--force]

Costs the same as a `--model` run: roughly two Statcast pulls per hitter, cached
on disk, so re-running the same day is cheap.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import main

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


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing snapshot even if it covers more games",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    date_str = args.date or datetime.today().strftime("%Y-%m-%d")
    path = snapshot_path(date_str)

    existing = None
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            existing = None

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

    modelled = sum(1 for p in snapshot["picks"] if p["prob_model"] is not None)
    print(
        f"Wrote {path} - {len(snapshot['picks'])} hitters, "
        f"{modelled} with a model value, {snapshot['games_upcoming']} upcoming games"
    )


if __name__ == "__main__":
    main_cli()
