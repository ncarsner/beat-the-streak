import requests
from bs4 import BeautifulSoup, Comment
from datetime import datetime, timedelta
from prettytable import PrettyTable
from time import sleep
import random
import configparser

from players import hitters


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Limit the number of players fetched per run for validation purposes.
# Increase or set to None to process all players in the provided pool.
MAX_PLAYERS = 10


class ServerError(Exception):
    """Raised when the server returns a rejection response (e.g. 403, 429, 5xx)."""


# Function to load config
def load_config(file_path="config.ini"):
    config = configparser.ConfigParser()
    config.read(file_path)
    return config


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

# base website
site_base = "https://www.baseball-reference.com/players/"


# Load config and set headers; fall back to a default User-Agent if config.ini is absent
config = load_config()
user_agent = config.get("browser", "user_agent", fallback=DEFAULT_USER_AGENT)
headers = {"User-Agent": user_agent}


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


def _find_last5_div(soup):
    """Return the div#div_last5 element, searching inside HTML comments if needed.

    Baseball Reference wraps secondary tables in HTML comments for lazy-loading;
    BeautifulSoup skips comment content by default so we search comments explicitly.
    """
    div = soup.find("div", id="div_last5")
    if div:
        return div
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment_soup = BeautifulSoup(comment, "html.parser")
        div = comment_soup.find("div", id="div_last5")
        if div:
            return div
    return None


# Scrape and summarize data for each player
def scrape_player_data(player, url):
    full_url = site_base + url
    try:
        response = requests.get(full_url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        print(f"connection error ({exc})")
        return None

    if response.status_code in (403, 429):
        raise ServerError(
            f"HTTP {response.status_code} — server is blocking requests"
        )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        print(f"HTTP error ({exc})")
        return None

    soup = BeautifulSoup(response.content, "html.parser")

    last5_div = _find_last5_div(soup)
    if not last5_div:
        return None

    rows = last5_div.select("table > tbody > tr")
    if not rows:
        return None

    # Use the date from the last game row (most recent)
    last_date_str = None
    for row in reversed(rows):
        date_elem = row.find("th", {"data-stat": "date_game"})
        if date_elem:
            raw = date_elem.text.strip()
            # Doubleheader dates appear as "YYYY-MM-DD (1)" — strip the suffix
            last_date_str = raw.split("(")[0].strip()
            if last_date_str:
                break

    if not last_date_str or not is_within_past_week(last_date_str):
        return None  # Skip players whose last game was more than a week ago

    at_bats = 0
    hits = 0
    walks = 0
    strikeouts = 0

    for row in rows:
        def _int(cell):
            if cell and cell.text.strip():
                try:
                    return int(cell.text.strip())
                except ValueError:
                    pass
            return 0

        at_bats += _int(row.find("td", {"data-stat": "AB"}))
        hits += _int(row.find("td", {"data-stat": "H"}))
        walks += _int(row.find("td", {"data-stat": "BB"}))
        strikeouts += _int(row.find("td", {"data-stat": "SO"}))

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
        players: Mapping of player name → Baseball-Reference URL slug.
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
        try:
            player_data = scrape_player_data(player, url)
        except ServerError as exc:
            print(f"\n[!] {exc}. Stopping further requests.")
            break

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
        sleep(random.uniform(1, 5))

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
