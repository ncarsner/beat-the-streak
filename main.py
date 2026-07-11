import argparse
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from prettytable import PrettyTable
from time import sleep
import random

from players import hitters


# MLB Stats API — official, free JSON API; no scraping, no bot-blocking
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# Limit the number of players fetched per run for validation purposes.
# Increase or set to None to process all players in the provided pool.
MAX_PLAYERS = 10

# Where "no recent data" results are remembered between runs.
NO_DATA_CACHE_FILE = Path(__file__).parent / ".cache" / "no_data_cache.json"

# Days a player stays skipped after polling with no recent data — a fresh
# game log won't have accumulated in less time than this. Overridable per
# run via `--cooldown-days`.
DEFAULT_COOLDOWN_DAYS = 7


selected_hitters = [  # narrow hitters
    "Luis Arraez",
    "Mookie Betts",
    "Xander Bogaerts",
    "Manny Machado",
    "Jonathan India",
    "Elly De La Cruz",
    "Tyler Stephenson",
    "TJ Friedl",
    "Ty France",
    "Jeimer Candelario",
    "Xavier Edwards",
    "Jake Burger",
    "Jonah Bride",
    "LaMonte Wade Jr",
    "Heliot Ramos",
    "Michael Conforto",
    "CJ Abrams",
    "George Springer",
    "Vladimir Guerrero Jr",
    "Ernie Clement",
    "Jackson Chourio",
    "Austin Riley",
    "Matt Olson",
    "Masyn Winn",
    "Bobby Witt Jr",
]

# Subset of hitters filters from selected_hitters list
selected_hitters = {key: hitters[key] for key in selected_hitters if key in hitters}

# Cache player-ID lookups so the search endpoint is only hit once per name per run
_player_id_cache: dict = {}


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


def is_in_cooldown(player, cache, cooldown_days):
    """True if *player* polled with no recent data too recently to be worth rechecking."""
    last_checked = cache.get(player)
    if not last_checked:
        return False
    last_checked_date = datetime.strptime(last_checked, "%Y-%m-%d")
    return (datetime.now() - last_checked_date) < timedelta(days=cooldown_days)


def lookup_player_id(name: str):
    """Return the MLB Stats API numeric player ID for *name*, or None if not found."""
    if name in _player_id_cache:
        return _player_id_cache[name]
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/search",
            params={"names": name},
            timeout=5,
        )
        resp.raise_for_status()
        people = resp.json().get("people", [])
        if people:
            player_id = people[0]["id"]
            _player_id_cache[name] = player_id
            return player_id
    except requests.RequestException:
        pass
    return None


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


def scrape_player_data(player, _url):
    """Fetch last-5-game batting stats for *player* from the MLB Stats API."""
    season = datetime.today().year
    player_id = lookup_player_id(player)
    if not player_id:
        return None

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

    # splits are ordered oldest → newest; take the last 5 games
    last5 = splits[-5:]

    last_date_str = last5[-1].get("date", "")
    if not last_date_str or not is_within_past_week(last_date_str):
        return None

    at_bats = sum(g["stat"].get("atBats", 0) for g in last5)
    hits = sum(g["stat"].get("hits", 0) for g in last5)
    walks = sum(g["stat"].get("baseOnBalls", 0) for g in last5)
    strikeouts = sum(g["stat"].get("strikeOuts", 0) for g in last5)

    return {
        "Player": player,
        "At Bats": at_bats,
        "Hits": hits,
        "Walks": walks,
        "Strikeouts": strikeouts,
    }


def compile_player_data(players, limit: int | None = MAX_PLAYERS, cooldown_days=DEFAULT_COOLDOWN_DAYS, cache=None):
    """Fetch and aggregate batting stats for each player.

    Args:
        players:       Mapping of player name → value (value is unused; names drive lookups).
        limit:         Maximum number of players to process.  Pass ``None`` to
                       process the entire pool.  Defaults to ``MAX_PLAYERS``.
        cooldown_days: Days to skip a player after they poll with no recent data.
        cache:         {player: last_checked_date_str} dict, mutated in place —
                       players are added on a no-data result and cleared on success.
    """
    if cache is None:
        cache = {}

    summary_data = []
    player_list = list(players.items())
    if limit is not None:
        player_list = player_list[:limit]
    total = len(player_list)

    for i, (player, url) in enumerate(player_list, 1):
        print(f"[{i}/{total}] Fetching {player} ...", end=" ", flush=True)

        if is_in_cooldown(player, cache, cooldown_days):
            print(f"skipped (cooling off, retry after {cooldown_days}d)")
            continue

        player_data = scrape_player_data(player, url)

        # Validates data returned and that at-bats are non-zero before computing probability
        # (and, optionally) if player's walks >= strikeouts
        if player_data and player_data["At Bats"] > 0:  # and player_data["Walks"] >= player_data["Strikeouts"]:
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
    top_players = summary_data[:n*2]

    # n lowest probability players
    low_players = summary_data[-n:]

    # Create and populate the table
    table = PrettyTable()
    today = datetime.today()
    table.title = f"{today.strftime('%B')} {today.day}, {today.year}"
    table.field_names = ["Player", "H-AB", "BB/K", "Prob %"]

    for data in top_players:
        probability = f"{data['probability']:.1%}"
        table.add_row(
            [
                data["Player"],
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
                f"{data['Hits']}-{data['At Bats']}",
                f"{data['Walks']}/{data['Strikeouts']}",
                probability,
            ]
        )

    # Display the output
    print(table)


def resolve_run_config(mode):
    """Return the (players, limit) pair for a given --mode."""
    if mode == "subset":
        return selected_hitters, MAX_PLAYERS
    if mode == "max":
        return hitters, MAX_PLAYERS
    if mode == "full":
        return hitters, None
    raise ValueError(f"Unknown mode: {mode}")


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Beat the Streak — hit-probability ranking tool")
    parser.add_argument(
        "--mode",
        choices=["subset", "max", "full"],
        default="subset",
        help=(
            "subset: curated selected_hitters list, capped at MAX_PLAYERS (default); "
            "max: full player pool, capped at MAX_PLAYERS; "
            "full: full player pool, uncapped"
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
    players, limit = resolve_run_config(args.mode)

    no_data_cache = load_no_data_cache()
    summary = compile_player_data(
        players=players,
        limit=limit,
        cooldown_days=args.cooldown_days,
        cache=no_data_cache,
    )
    save_no_data_cache(no_data_cache)

    probable_hitters(summary, n=5)
