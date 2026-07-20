import argparse
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from prettytable import PrettyTable
from time import sleep
import random



# MLB Stats API — official, free JSON API; no scraping, no bot-blocking
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# Limit the number of players fetched per run for validation purposes.
# Increase or set to None to process all players in the provided pool.
MAX_PLAYERS = 10

# Where "no recent data" results are remembered between runs.
NO_DATA_CACHE_FILE = Path(__file__).parent / ".cache" / "no_data_cache.json"

# Where crosswalk misses are persisted for later review.
MISSING_TEAM_CACHE_FILE = Path(__file__).parent / ".cache" / "missing_team_cache.json"

# Where /schedule request failures are logged for later review.
SCHEDULE_ERROR_LOG_FILE = Path(__file__).parent / ".cache" / "schedule_fetch_errors.log"

# Days a player stays skipped after polling with no recent data — a fresh
# game log won't have accumulated in less time than this. Overridable per
# run via `--cooldown-days`.
DEFAULT_COOLDOWN_DAYS = 7


def load_no_data_cache(path=NO_DATA_CACHE_FILE):
    """Return the {player: last_checked_date_str} cache from a prior run, or {} if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_no_data_cache(cache, path=NO_DATA_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_missing_team_cache(path=MISSING_TEAM_CACHE_FILE):
    """Return the {team_name: {first_seen, players}} cache from a prior run, or {} if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_missing_team_cache(cache, path=MISSING_TEAM_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def log_schedule_fetch_error(exc, path=SCHEDULE_ERROR_LOG_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a") as f:
        f.write(f"{timestamp} — {exc}\n")


def fetch_schedule(date: str) -> list[dict]:
    """Return per-game records for all non-postponed games on *date* (YYYY-MM-DD).

    Each record: {gamePk, gameNumber, home_team_id, away_team_id, start_dt}.
    Both games of a doubleheader appear as distinct entries.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "date": date},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"connection error ({exc})")
        log_schedule_fetch_error(exc)
        return []
    dates = resp.json().get("dates", [])
    if not dates:
        return []
    schedule = []
    for game in dates[0].get("games", []):
        if game.get("status", {}).get("detailedState") == "Postponed":
            continue
        start_dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
        schedule.append(
            {
                "gamePk": game["gamePk"],
                "gameNumber": game.get("gameNumber", 1),
                "home_team_id": game["teams"]["home"]["team"]["id"],
                "away_team_id": game["teams"]["away"]["team"]["id"],
                "start_dt": start_dt,
            }
        )
    return schedule


def fetch_lineup(game_pk: int) -> dict[str, list[dict]]:
    """Return posted batting-order players for *game_pk* keyed by "home" and "away".

    Each player dict: {id, fullName, team_id}. A team with an empty or absent
    battingOrder returns an empty list for that side. Returns {"home": [], "away": []}
    on request failure without raising.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/game/{game_pk}/boxscore",
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"lineup fetch error for gamePk={game_pk} ({exc})")
        log_schedule_fetch_error(exc)
        return {"home": [], "away": []}

    teams_data = resp.json().get("teams", {})
    result: dict[str, list[dict]] = {}
    for side in ("home", "away"):
        team = teams_data.get(side, {})
        batting_order = team.get("battingOrder") or []
        team_players = team.get("players", {})
        side_list = []
        for player_id in batting_order:
            info = team_players.get(f"ID{player_id}", {})
            side_list.append(
                {
                    "id": player_id,
                    "fullName": info.get("person", {}).get("fullName", ""),
                    "team_id": info.get("parentTeamId"),
                }
            )
        result[side] = side_list
    return result


def select_games(
    games: list[dict], now: datetime, scheduled: bool = False
) -> list[dict]:
    """Filter *games* to those relevant for this run.

    Manual mode (scheduled=False): games with start_dt >= now.
    Scheduled mode (scheduled=True): games where 0 < start_dt - now <= 2 hours.
    """
    if scheduled:
        window = timedelta(hours=2)
        return [g for g in games if timedelta(0) < g["start_dt"] - now <= window]
    return [g for g in games if g["start_dt"] >= now]


def is_in_cooldown(player, cache, cooldown_days):
    """True if *player* polled with no recent data too recently to be worth rechecking."""
    last_checked = cache.get(player)
    if not last_checked:
        return False
    last_checked_date = datetime.strptime(last_checked, "%Y-%m-%d")
    return (datetime.now() - last_checked_date) < timedelta(days=cooldown_days)


def is_within_past_week(date_str):
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    one_week_ago = datetime.now() - timedelta(days=7)
    return date_obj >= one_week_ago


def binomial_probability(ab, h, bb):
    if ab == 0:
        return 0.0
    pa = ab + bb
    exp = pa / 5
    avg = h / ab
    return 1 - (1 - avg) ** exp


def scrape_player_data(player, _url, missing_team_cache=None, schedule_map=None):
    # Placeholder: reworked in the id-based-lineup-input task (priority 7).
    return None


def compile_player_data(
    players,
    limit: int | None = MAX_PLAYERS,
    cooldown_days=DEFAULT_COOLDOWN_DAYS,
    cache=None,
    missing_team_cache=None,
    schedule_map=None,
):
    """Fetch and aggregate batting stats for each player.

    Args:
        players:            Mapping of player name → value (value is unused; names drive lookups).
        limit:              Maximum number of players to process.  Pass ``None`` to
                            process the entire pool.  Defaults to ``MAX_PLAYERS``.
        cooldown_days:      Days to skip a player after they poll with no recent data.
        cache:              {player: last_checked_date_str} dict, mutated in place —
                            players are added on a no-data result and cleared on success.
        missing_team_cache: {team_name: {first_seen, players}} dict, mutated in place —
                            updated when a player's team_name is not found in TEAM_CROSSWALK.
        schedule_map:       {team_id: game_hour_utc} dict derived from fetch_schedule,
                            threaded through to scrape_player_data unchanged.
    """
    if cache is None:
        cache = {}

    summary_data = []
    player_list = list(players.items())
    if limit is not None:
        player_list = player_list[:limit]
    total = len(player_list)

    for i, (player, url) in enumerate(player_list, 1):
        if is_in_cooldown(player, cache, cooldown_days):
            continue

        print(f"[{i}/{total}] Fetching {player} ...", end=" ", flush=True)
        player_data = scrape_player_data(player, url, missing_team_cache, schedule_map)

        # Validates data returned and that at-bats are non-zero before computing probability
        # (and, optionally) if player's walks >= strikeouts
        if (
            player_data and player_data["At Bats"] > 0
        ):  # and player_data["Walks"] >= player_data["Strikeouts"]:
            player_data["probability"] = binomial_probability(
                player_data["At Bats"], player_data["Hits"], player_data["Walks"]
            )
            summary_data.append(player_data)
            print(f"ok  ({player_data['Hits']}-{player_data['At Bats']})")
            cache.pop(player, None)
        else:
            print("skipped (no recent data)")
            cache[player] = datetime.now().strftime("%Y-%m-%d")
        sleep(random.uniform(0.6, 1.8))

    return summary_data


def probable_hitters(summary_data, n=5):
    # Sort summary data based on descending probability
    summary_data.sort(key=lambda x: x["probability"], reverse=True)

    # n highest probability players
    top_players = summary_data[: n * 2]

    # n lowest probability players
    low_players = summary_data[-n:]

    # Create and populate the table
    table = PrettyTable()
    today = datetime.today()
    table.title = f"{today.strftime('%B')} {today.day}, {today.year}"
    table.field_names = ["Player", "Team", "H-AB", "BB/K", "Prob %"]

    for data in top_players:
        probability = f"{data['probability']:.1%}"
        table.add_row(
            [
                data["Player"],
                data["Team"],
                f"{data['Hits']}-{data['At Bats']}",
                f"{data['Walks']}/{data['Strikeouts']}",
                probability,
            ]
        )

    # Separator row
    table.add_row(["---"] * len(table.field_names))

    for data in low_players:
        probability = f"{data['probability']:.1%}"
        table.add_row(
            [
                data["Player"],
                data["Team"],
                f"{data['Hits']}-{data['At Bats']}",
                f"{data['Walks']}/{data['Strikeouts']}",
                probability,
            ]
        )

    # Display the output
    print(table)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Beat the Streak — hit-probability ranking tool"
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help=(
            "Scheduled mode: only process games starting within the next 2 hours. "
            "Without this flag, all games starting at or after now are included (manual mode)."
        ),
    )
    parser.add_argument(
        "--cooldown-days",
        type=int,
        default=DEFAULT_COOLDOWN_DAYS,
        help=f"Days to skip a player after a no-data poll before rechecking (default: {DEFAULT_COOLDOWN_DAYS})",
    )
    return parser


if __name__ == "__main__":
    args = build_arg_parser().parse_args()

    today = datetime.today().strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)
    games = fetch_schedule(today)
    selected = select_games(games, now, scheduled=args.scheduled)

    schedule_map = {}
    for g in selected:
        hour = g["start_dt"].hour
        schedule_map[g["home_team_id"]] = hour
        schedule_map[g["away_team_id"]] = hour

    no_data_cache = load_no_data_cache()
    missing_team_cache = load_missing_team_cache()
    summary = compile_player_data(
        players={},
        limit=MAX_PLAYERS,
        cooldown_days=args.cooldown_days,
        cache=no_data_cache,
        missing_team_cache=missing_team_cache,
        schedule_map=schedule_map,
    )
    save_no_data_cache(no_data_cache)
    save_missing_team_cache(missing_team_cache)

    probable_hitters(summary, n=5)
