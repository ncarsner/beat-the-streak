"""Grade recorded picks against what actually happened, and score both methods.

The second half of the forward test begun by `scripts/record_daily_picks.py`.
Reads every snapshot in `data/picks/`, fetches the realized outcome for each
hitter, and reports how the shipped `binomial_probability` heuristic and the
calculator model compare.

The outcome being predicted is **at least one hit**, which is what Beat the
Streak asks and what both probabilities claim to estimate. It comes from
`GET /game/{gamePk}/boxscore`, the same endpoint `main.fetch_lineup` already
uses, at one request per game per day.

Three numbers, and they answer different questions:

  top-1 / top-5 hit rate   How often the method's best picks got a hit. This is
                           the question the tool exists to answer, since a user
                           picks one hitter a day.
  Brier score              Mean squared error of the probability against the
                           0/1 outcome. Lower is better. Measures calibration
                           and discrimination together.
  log loss                 Punishes confident wrong answers far harder than
                           Brier does. Reported because the heuristic is the
                           confident one: it puts hitters at 92% and 19% where
                           the model spans 50% to 74%.

**A ranking method can win on hit rate and lose on Brier, and that is not a
contradiction.** Brier rewards saying 60% when the true rate is 60%; hit rate
only cares about the ordering. The tool ranks, so hit rate is the primary
measure and the scores are diagnostic.

Hitters who did not play are dropped, not scored as failures. A scratched hitter
is not a wrong prediction, it is an absent one, and counting it as a miss would
punish whichever method liked him. Games not yet final are skipped entirely so a
partial day never enters the totals.

Usage::

    uv run python3 scripts/grade_daily_picks.py [--date YYYY-MM-DD] [--top-n 5]
"""

import argparse
import json
import math
import sys
from pathlib import Path

import requests
from prettytable import PrettyTable

# Running this file by path puts `scripts/` on `sys.path`, not the repo root, so
# `import mlb_api` raises ModuleNotFoundError. Prepending the repo root makes a
# bare `python3 scripts/grade_daily_picks.py` behave like `PYTHONPATH=. python3
# ...`, which is what the usage line used to require.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_api import MLB_API_BASE  # noqa: E402 - must follow the bootstrap above

PICKS_DIR = Path(__file__).resolve().parent.parent / "data" / "picks"

# Below this, a difference between two methods is noise. Stated so a reader does
# not over-read an early lead: roughly 10 picks a day means weeks, not days.
MIN_GRADED_DAYS_FOR_SIGNAL = 20


def fetch_final_game_pks(date_str: str) -> set[int]:
    """Return the gamePks on *date_str* that have finished.

    One request for the whole day rather than one per game, and it is the only
    thing standing between this script and silently grading unplayed games.

    **A Pre-Game boxscore already publishes a fully zeroed batting line** for
    every hitter in a posted lineup: `hits: 0`, `atBats: 0`, `gamesPlayed: 1`.
    Nothing in the boxscore itself distinguishes that from a real 0-for-4, so a
    grader that reads the boxscore alone scores every pick as a miss hours
    before first pitch and reports a 0% hit rate with total confidence. The
    status has to come from the schedule, which is the only source that carries
    it.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "date": date_str},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  schedule fetch failed for {date_str} ({exc})")
        return set()

    return {
        game["gamePk"]
        for entry in resp.json().get("dates", [])
        for game in entry.get("games", [])
        if game.get("status", {}).get("abstractGameState") == "Final"
    }


def fetch_game_outcomes(game_pk: int) -> dict[int, bool] | None:
    """Return {player_id: got_a_hit} for one finished game, or None on failure.

    Only call this for a gamePk `fetch_final_game_pks` returned. A player with
    no plate appearance in a completed game did not bat, so he is omitted here
    and dropped by the caller rather than counted as a failed prediction.
    """
    try:
        resp = requests.get(f"{MLB_API_BASE}/game/{game_pk}/boxscore", timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  boxscore fetch failed for gamePk={game_pk} ({exc})")
        return None

    payload = resp.json()
    outcomes: dict[int, bool] = {}
    for side in ("home", "away"):
        players = payload.get("teams", {}).get(side, {}).get("players", {})
        for info in players.values():
            batting = info.get("stats", {}).get("batting", {})
            if not batting:
                continue
            # A walk is a plate appearance with no at-bat, and the hitter did
            # play, so key on plate appearances rather than at-bats.
            if not (batting.get("plateAppearances") or 0):
                continue
            player_id = info.get("person", {}).get("id")
            if player_id is None:
                continue
            outcomes[player_id] = (batting.get("hits") or 0) > 0
    return outcomes


def _brier(probability: float, outcome: bool) -> float:
    return (probability - (1.0 if outcome else 0.0)) ** 2


def _log_loss(probability: float, outcome: bool) -> float:
    # Clamp away from the asymptotes: a method that says 0.0 and is wrong would
    # otherwise contribute infinity and destroy the whole average.
    p = min(max(probability, 1e-6), 1 - 1e-6)
    return -(math.log(p) if outcome else math.log(1 - p))


def grade_snapshot(snapshot: dict) -> dict | None:
    """Grade one day. Returns None when no game in it is final yet.

    A day is graded on whichever of its games have finished. A partially final
    day is legitimate (an afternoon game is over while a night game is not) and
    the unfinished games simply contribute nothing until a later run.
    """
    snapshot_pks = {p["game_pk"] for p in snapshot["picks"] if p.get("game_pk")}
    final_pks = fetch_final_game_pks(snapshot["date"]) & snapshot_pks
    if not final_pks:
        return None

    pending = len(snapshot_pks) - len(final_pks)
    if pending:
        print(
            f"  {len(final_pks)} of {len(snapshot_pks)} games final, {pending} pending"
        )

    outcomes: dict[int, bool] = {}
    for game_pk in sorted(final_pks):
        result = fetch_game_outcomes(game_pk)
        if result:
            outcomes.update(result)
    if not outcomes:
        return None

    graded = []
    for pick in snapshot["picks"]:
        got_hit = outcomes.get(pick.get("player_id"))
        if got_hit is None:
            continue  # did not play; absent, not wrong
        graded.append({**pick, "got_hit": got_hit})
    if not graded:
        return None

    return {"date": snapshot["date"], "graded": graded}


def render_day(day: dict, top_n: int) -> str:
    """Return one day's graded picks as a table, each method's own top *top_n*.

    This is the row-level view behind the summary: it shows which hitter each
    method actually picked and whether he delivered, which is the thing a
    person can sanity-check. The summary alone cannot show a day where both
    methods were right for different reasons.
    """
    lines = []
    for label, key in (("Prob %", "prob_heuristic"), ("Model", "prob_model")):
        ranked = [p for p in day["graded"] if p.get(key) is not None]
        if not ranked:
            continue
        ranked.sort(key=lambda p: p[key], reverse=True)
        table = PrettyTable()
        hits = sum(1 for p in ranked[:top_n] if p["got_hit"])
        table.title = (
            f"{day['date']}: top {top_n} by {label} ({hits}/{len(ranked[:top_n])} hit)"
        )
        table.field_names = ["#", "Player", "Tm", "Prob %", "Model", "Result"]
        table.align["Player"] = "l"
        for i, p in enumerate(ranked[:top_n], 1):
            model = p.get("prob_model")
            table.add_row(
                [
                    i,
                    p["player"],
                    p["team"],
                    f"{p['prob_heuristic']:.1%}",
                    f"{model:.1%}" if model is not None else "-",
                    "HIT" if p["got_hit"] else "no hit",
                ]
            )
        lines.append(str(table))
    return "\n".join(lines)


def summarize(days: list[dict], top_n: int) -> None:
    """Print the comparison. Every number here is descriptive, not a verdict."""
    methods = {"heuristic": "prob_heuristic", "model": "prob_model"}
    print(f"\nGraded days: {len(days)}")
    total_picks = sum(len(d["graded"]) for d in days)
    print(f"Graded hitter-games: {total_picks}")

    base = [p["got_hit"] for d in days for p in d["graded"]]
    if base:
        print(f"Base rate (any graded hitter gets a hit): {sum(base) / len(base):.1%}")

    table = PrettyTable()
    table.title = "Heuristic vs model, on realized outcomes"
    # `--top-n 1` makes the two hit-rate columns the same name, and PrettyTable
    # rejects duplicate field names outright, so the whole summary crashes.
    # Collapse to a single column when they would coincide.
    show_topn = top_n != 1
    table.field_names = (
        ["Method", "top-1", f"top-{top_n}", "Brier", "log loss", "scored"]
        if show_topn
        else ["Method", "top-1", "Brier", "log loss", "scored"]
    )
    table.align["Method"] = "l"

    for label, key in methods.items():
        top1_hits = top1_n = 0
        topn_hits = topn_n = 0
        briers: list[float] = []
        losses: list[float] = []

        for day in days:
            ranked = [p for p in day["graded"] if p.get(key) is not None]
            if not ranked:
                continue
            ranked.sort(key=lambda p: p[key], reverse=True)

            top1_hits += 1 if ranked[0]["got_hit"] else 0
            top1_n += 1
            for pick in ranked[:top_n]:
                topn_hits += 1 if pick["got_hit"] else 0
                topn_n += 1
            for pick in ranked:
                briers.append(_brier(pick[key], pick["got_hit"]))
                losses.append(_log_loss(pick[key], pick["got_hit"]))

        row = [
            label,
            f"{top1_hits}/{top1_n} ({top1_hits / top1_n:.0%})" if top1_n else "-",
        ]
        if show_topn:
            row.append(
                f"{topn_hits}/{topn_n} ({topn_hits / topn_n:.0%})" if topn_n else "-"
            )
        row += [
            f"{sum(briers) / len(briers):.4f}" if briers else "-",
            f"{sum(losses) / len(losses):.4f}" if losses else "-",
            len(briers),
        ]
        table.add_row(row)

    print(table)
    print(
        "\nLower Brier and log loss are better; higher hit rates are better."
        "\nThe tool ranks, so the hit rates are the primary measure."
    )
    if len(days) < MIN_GRADED_DAYS_FOR_SIGNAL:
        print(
            f"\nNOTE: {len(days)} graded day(s). At roughly ten picks a day, a gap "
            f"between two methods is not meaningful below about "
            f"{MIN_GRADED_DAYS_FOR_SIGNAL} days. Keep recording."
        )


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="grade only this YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument(
        "--detail",
        action="store_true",
        help="also show each graded day's picks and results row by row",
    )
    args = parser.parse_args()

    if not PICKS_DIR.exists():
        print(f"No snapshots found at {PICKS_DIR}. Run record_daily_picks.py first.")
        return

    paths = sorted(PICKS_DIR.glob("*.json"))
    if args.date:
        paths = [p for p in paths if p.stem == args.date]

    days = []
    for path in paths:
        try:
            snapshot = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Skipping {path.name}: unreadable ({exc})")
            continue
        print(f"Grading {path.stem} ...")
        graded = grade_snapshot(snapshot)
        if graded is None:
            print("  no final games yet, skipped")
            continue
        days.append(graded)

    if not days:
        print("\nNothing gradeable yet.")
        return

    if args.detail:
        for day in days:
            print()
            print(render_day(day, args.top_n))
    summarize(days, args.top_n)


if __name__ == "__main__":
    main_cli()
