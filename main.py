import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from prettytable import PrettyTable
from time import sleep
import random
import configparser

from players import hitters


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


# Load config and set headers
config = load_config()
headers = {"User-Agent": config["browser"]["user_agent"]}


def is_within_past_week(date_str):
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    one_week_ago = datetime.now() - timedelta(days=7)
    return date_obj >= one_week_ago


def binomial_probability(ab, h, bb):
    pa = ab + bb
    exp = pa / 5
    avg = h / ab
    return 1 - (1 - avg) ** exp


# Scrape and summarize data for each player
def scrape_player_data(player, url):
    full_url = site_base + url
    response = requests.get(full_url, headers=None)
    soup = BeautifulSoup(response.content, "html.parser")

    # Find the date in the specified XPath location
    date_elem = soup.select_one("#div_last5 > table > tbody > tr:nth-of-type(5) > th")
    if date_elem:
        date_str = date_elem.text.strip()
        if not is_within_past_week(date_str):
            return None  # Ignore if span is older than one week
    else:
        return None  # Ignore if date element is not found

    # Find the table rows within the specified div
    rows = soup.select("#div_last5 > table > tbody > tr")

    at_bats = 0
    hits = 0
    walks = 0
    strikeouts = 0

    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 12:
            total_atbats = cols[5].text
            total_hits = cols[7].text
            total_walks = cols[12].text
            total_strikeouts = cols[13].text

            if total_atbats:
                at_bats += int(total_atbats)
            if total_hits:
                hits += int(total_hits)
            if total_walks:
                walks += int(total_walks)
            if total_strikeouts:
                strikeouts += int(total_strikeouts)

    return {
        "Player": player,
        "At Bats": at_bats,
        "Hits": hits,
        "Walks": walks,
        "Strikeouts": strikeouts,
    }


def compile_player_data(players):
    summary_data = []
    for player, url in players.items():
        player_data = scrape_player_data(player, url)
        # Validates data returned, (and, optionally) if player's walks >= strikeouts
        if player_data: # and player_data["Walks"] >= player_data["Strikeouts"]:
            player_data["probability"] = binomial_probability(
                player_data["At Bats"], player_data["Hits"], player_data["Walks"]
            )
            summary_data.append(player_data)
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
    table.title = datetime.today().strftime("%B %#d, %Y")
    table.field_names = ["Player", "H-AB", "BB/K", "Prob %"]

    for data in top_players:
        probability = f"{data["probability"]:.1%}"
        table.add_row(
            [
                data["Player"],
                f"{data["Hits"]}-{data["At Bats"]}",
                f"{data["Walks"]}/{data["Strikeouts"]}",
                probability,
            ]
        )

    # Separator row
    table.add_row(["---"] * len(table.field_names))

    for data in low_players:
        probability = f"{data["probability"]:.1%}"
        table.add_row(
            [
                data["Player"],
                f"{data["Hits"]}-{data["At Bats"]}",
                f"{data["Walks"]}/{data["Strikeouts"]}",
                probability,
            ]
        )

    # Display the output
    print(table)


if __name__ == "__main__":
    probable_hitters(compile_player_data(players=hitters), n=5)
