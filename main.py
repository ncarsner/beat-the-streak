import requests
from datetime import datetime, timedelta
from prettytable import PrettyTable
from time import sleep
import random

from players import hitters


# MLB Stats API — official, free JSON API; no scraping, no bot-blocking
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

# Limit the number of players fetched per run for validation purposes.
# Increase or set to None to process all players in the provided pool.
MAX_PLAYERS = 10


selected_hitters = [  # narrow hitters
    "Luis Arraez",
    "Jurickson Profar",
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
    "Juan Yepez",
    "Alex Call",
    "George Springer",
    "Vladimir Guerrero Jr",
    "Ernie Clement",
    "Rhys Hoskins",
    "Jackson Chourio",
    "Jorge Soler",
    "Austin Riley",
    "Marcell Ozuna",
    "Matt Olson",
    "Masyn Winn",
    "Bobby Witt Jr",
    "Vinnie Pasquantino",
    "Salvador Perez",
    "Charlie Blackmon",
    "Ezequiel Tovar",
]

# Subset of hitters filters from selected_hitters list
selected_hitters = {key: hitters[key] for key in selected_hitters if key in hitters}

# Cache player-ID lookups so the search endpoint is only hit once per name per run
_player_id_cache: dict = {}


def lookup_player_id(name: str):
    """Return the MLB Stats API numeric player ID for *name*, or None if not found."""
    if name in _player_id_cache:
        return _player_id_cache[name]
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/search",
            params={"names": name},
            timeout=10,
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

    splits = resp.json().get("stats", [{}])[0].get("splits", [])
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


def compile_player_data(players, limit=MAX_PLAYERS):
    """Fetch and aggregate batting stats for each player.

    Args:
        players: Mapping of player name → value (value is unused; names drive lookups).
        limit:   Maximum number of players to process.  Pass ``None`` to
                 process the entire pool.  Defaults to ``MAX_PLAYERS``.
    """
    summary_data = []
    player_list = list(players.items())
    if limit is not None:
        player_list = player_list[:limit]
    total = len(player_list)

    for i, (player, url) in enumerate(player_list, 1):
        print(f"[{i}/{total}] Fetching {player} ...", end=" ", flush=True)
        player_data = scrape_player_data(player, url)

        # Validates data returned and that at-bats are non-zero before computing probability
        # (and, optionally) if player's walks >= strikeouts
        if player_data and player_data["At Bats"] > 0:  # and player_data["Walks"] >= player_data["Strikeouts"]:
            player_data["probability"] = binomial_probability(
                player_data["At Bats"], player_data["Hits"], player_data["Walks"]
            )
            summary_data.append(player_data)
            print(f"ok  ({player_data['Hits']}-{player_data['At Bats']})")
        else:
            print("skipped (no recent data)")
        sleep(random.uniform(0.5, 1.5))

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


if __name__ == "__main__":
    # selected_hitters is a curated subset; use hitters for the full player pool.
    # MAX_PLAYERS caps the run for validation before scaling up.
    probable_hitters(compile_player_data(players=selected_hitters, limit=MAX_PLAYERS), n=5)
