import argparse
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from prettytable import PrettyTable
from time import sleep
import random

from teams import TEAM_ID_TO_ABBR


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


def scrape_player_data(
    player_id: int,
    player_name: str,
    team_id: int,
    schedule_map: dict | None = None,
) -> dict | None:
    """Fetch last-5-game batting stats for *player_name* from the MLB Stats API."""
    season = datetime.today().year
    team_abbr = TEAM_ID_TO_ABBR.get(team_id, "???")

    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/{player_id}/stats",
            params={
                "stats": "gameLog",
                "group": "hitting",
                "season": season,
                "gameType": "R",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"connection error ({exc})")
        return None

    stats_list = resp.json().get("stats", [])
    if not stats_list:
        return None
    splits = stats_list[0].get("splits", [])
    if not splits:
        return None

    # splits ordered oldest → newest; take the last 5 games
    last5 = splits[-5:]
    last_date_str = last5[-1].get("date", "")
    if not last_date_str or not is_within_past_week(last_date_str):
        return None

    at_bats = sum(g["stat"].get("atBats", 0) for g in last5)
    hits = sum(g["stat"].get("hits", 0) for g in last5)
    walks = sum(g["stat"].get("baseOnBalls", 0) for g in last5)
    strikeouts = sum(g["stat"].get("strikeOuts", 0) for g in last5)

    game_hour = schedule_map.get(team_id) if schedule_map is not None else None
    return {
        "Player": player_name,
        "Team": team_abbr,
        "At Bats": at_bats,
        "Hits": hits,
        "Walks": walks,
        "Strikeouts": strikeouts,
        "GameHourUTC": game_hour,
    }


def compile_player_data(
    players: list[dict],
    limit: int | None = MAX_PLAYERS,
    cooldown_days=DEFAULT_COOLDOWN_DAYS,
    cache=None,
    missing_team_cache=None,
    schedule_map=None,
):
    """Fetch and aggregate batting stats for each player.

    Args:
        players:            List of player dicts with {id, fullName, team_id} from lineup fetch.
        limit:              Maximum number of players to process.  Pass ``None`` to
                            process the entire pool.  Defaults to ``MAX_PLAYERS``.
        cooldown_days:      Days to skip a player after they poll with no recent data.
        cache:              {player_name: last_checked_date_str} dict, mutated in place —
                            players are added on a no-data result and cleared on success.
        missing_team_cache: Retained for task-10 cleanup; unused by this function.
        schedule_map:       {team_id: game_hour_utc} dict derived from fetch_schedule,
                            threaded through to scrape_player_data unchanged.
    """
    if cache is None:
        cache = {}

    summary_data = []
    player_list = players if limit is None else players[:limit]
    total = len(player_list)

    for i, player in enumerate(player_list, 1):
        name = player["fullName"]
        if is_in_cooldown(name, cache, cooldown_days):
            continue

        print(f"[{i}/{total}] Fetching {name} ...", end=" ", flush=True)
        player_data = scrape_player_data(
            player["id"], name, player["team_id"], schedule_map
        )

        if player_data and player_data["At Bats"] > 0:
            player_data["probability"] = binomial_probability(
                player_data["At Bats"], player_data["Hits"], player_data["Walks"]
            )
            summary_data.append(player_data)
            print(f"ok  ({player_data['Hits']}-{player_data['At Bats']})")
            cache.pop(name, None)
        else:
            print("skipped (no recent data)")
            cache[name] = datetime.now().strftime("%Y-%m-%d")
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
    all_players: list[dict] = []
    for g in selected:
        hour = g["start_dt"].hour
        schedule_map[g["home_team_id"]] = hour
        schedule_map[g["away_team_id"]] = hour
        lineup = fetch_lineup(g["gamePk"])
        all_players.extend(lineup["home"])
        all_players.extend(lineup["away"])

    no_data_cache = load_no_data_cache()
    missing_team_cache = load_missing_team_cache()
    summary = compile_player_data(
        players=all_players,
        limit=MAX_PLAYERS,
        cooldown_days=args.cooldown_days,
        cache=no_data_cache,
        missing_team_cache=missing_team_cache,
        schedule_map=schedule_map,
    )
    save_no_data_cache(no_data_cache)
    save_missing_team_cache(missing_team_cache)

    probable_hitters(summary, n=5)
