import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from prettytable import PrettyTable
from time import sleep
import random

from players import hitters


# base website
site_base = "https://www.baseball-reference.com/players/"


# Headers to include in the requests
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
}


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
    response = requests.get(full_url, headers=headers)
    soup = BeautifulSoup(response.content, "html.parser")

    # Find the date in the specified XPath location
    date_elem = soup.select_one("#div_last5 > table > tbody > tr:nth-of-type(5) > th")
    if date_elem:
        # print(date_elem, date_elem.text.strip()) # DEBUG
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

    for row in rows[:5]:  # Get the first 5 rows
        cols = row.find_all("td")
        if len(cols) >= 12:
            total_atbats = cols[5].text
            total_hits = cols[7].text
            total_walks = cols[12].text

            if total_atbats:
                at_bats += int(total_atbats)
            if total_hits:
                hits += int(total_hits)
            if total_walks:
                walks += int(total_walks)

    return {"Player": player, "At Bats": at_bats, "Hits": hits, "Walks": walks}


def compile_player_data(players=hitters):
    summary_data = []
    for player, url in players.items():
        player_data = scrape_player_data(player, url)
        if player_data:
            player_data["probability"] = binomial_probability(
                player_data["At Bats"], player_data["Hits"], player_data["Walks"]
            )
            summary_data.append(player_data)
        sleep(random.uniform(1, 5))

    return summary_data


def probable_hitters(summary_data, n=5):
    # Sort summary data based on descending probability
    summary_data.sort(key=lambda x: x["probability"], reverse=True)

    # Print the top n players based on highest probability
    top_players = summary_data[:n]

    # Create and populate the table
    table = PrettyTable()
    table.field_names = ["Player", "AB", "H", "BB", "Prob %"]

    for data in top_players:
        probability = f"{data["probability"]:.1%}"
        table.add_row(
            [data["Player"], data["At Bats"], data["Hits"], data["Walks"], probability]
        )

    # Display the output
    print(table)


if __name__ == "__main__":
    probable_hitters(compile_player_data(), n=8)
