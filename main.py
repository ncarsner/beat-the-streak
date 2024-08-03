# import datetime
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from prettytable import PrettyTable
from time import sleep
import random


# base website
site_base = "https://www.baseball-reference.com/players/"

# Players to evaluate
players = {
    "Bobby Witt Jr": "w/wittbo02.shtml",
    "Jose Altuve": "a/altuvjo01.shtml",
    "Rafael Devers": "d/deverra01.shtml",
    "Aaron Judge": "j/judgeaa01.shtml",
    "Manny Machado": "m/machama01.shtml",
    "Corbin Carroll": "c/carroco01.shtml",
    "Joc Pederson": "p/pederjo01.shtml",
    "Bryan Reynolds": "r/reynobr01.shtml",
    "Oneil Cruz": "c/cruzon01.shtml",
    "George Springer": "s/springe01.shtml",
    "Vladimir Guerrero Jr": "g/guerrvl02.shtml",
    "Juan Soto": "s/sotoju01.shtml",
    "Gleyber Torres": "t/torregl01.shtml",
    "Jazz Chisholm Jr": "c/chishja01.shtml",
    "Colton Cowser": "c/cowseco01.shtml",
    "Anthony Santander": "s/santaan02.shtml",
    "Gunnar Henderson": "h/hendegu01.shtml",
    "Ryan O'Hearn": "o/ohearry01.shtml",
    "Adley Rutschmann": "r/rutscad01.shtml",
    "Ryan Mountcastle": "m/mountry01.shtml",
    "Cedric Mullins": "m/mullice01.shtml",
    "Steven Kwan": "k/kwanst01.shtml",
    "Lane Thomas": "t/thomala02.shtml",
    "Jose Ramirez": "r/ramirjo01.shtml",
    "Josh Naylor": "n/naylojo01.shtml",
    "Mark Canha": "c/canhama01.shtml",
    "Heliot Ramos": "r/ramoshe02.shtml",
    "Jonathan India": "i/indiajo01.shtml",
    "Elly De La Cruz": "d/delacel01.shtml",
    "Spencer Steer": "s/steersp01.shtml",
    "Ty France": "f/francty01.shtml",
    "Jake Burger": "b/burgeja01.shtml",
    "Jorge Soler": "s/solerjo01.shtml",
    "Austin Riley": "r/rileyau01.shtml",
    "Marcell Ozuna": "o/ozunama01.shtml",
    "Matt Olson": "o/olsonma02.shtml",
    "Sean Murphy": "m/murphse01.shtml",
    "Jarren Duran": "d/duranja01.shtml",
    "Corey Seager": "s/seageco01.shtml",
    "Marcus Semien": "s/semiema01.shtml",
    "Josh Smith": "s/smithjo11.shtml",
    "Wyatt Langford": "l/langfwy01.shtml",
    "Yandy Diaz": "d/diazya01.shtml",
    "Dylan Carlson": "c/carlsdy01.shtml",
    "Brandon Lowe": "l/lowebr01.shtml",
    "Christopher Morel": "m/morelch01.shtml",
    "Alex Bregman": "b/bregmal01.shtml",
    "Yordan Alvarez": "a/alvaryo01.shtml",
    "Jeremy Pena": "p/penaje02.shtml",
    "Luis Robert Jr": "r/roberlu01.shtml",
    "Trevor Larnach": "l/larnatr01.shtml",
    "Byron Buxton": "b/buxtoby01.shtml",
    "Royce Lewis": "l/lewisro02.shtml",
    "Francisco Lindor": "l/lindofr01.shtml",
    "Brandon Nimmo": "n/nimmobr01.shtml",
    "J.D. Martinez": "m/martijd02.shtml",
    "Pete Alonso": "a/alonspe01.shtml",
    "Luis Rengifo": "r/rengilu01.shtml",
    "Shohei Ohtani": "o/ohtansh01.shtml",
    "Will Smith": "s/smithwi05.shtml",
    "Gavin Lux": "l/luxga01.shtml",
    "Teoscar Hernandez": "h/hernate01.shtml",
    "Miguel Andujar": "a/andujmi01.shtml",
    "Brent Rooker": "r/rookebr01.shtml",
    "Charlie Blackmon": "b/blackch02.shtml",
    "Ezequiel Tovar": "t/tovarez01.shtml",
    "Brenton Doyle": "d/doylebr02.shtml",
    "Jurickson Profar": "p/profaju01.shtml",
    "Xander Bogaerts": "b/bogaexa01.shtml",
    "Jake Cronenworth": "c/croneja01.shtml",
    "Kyle Schwarber": "s/schwaky01.shtml",
    "Trea Turner": "t/turnetr01.shtml",
    "Bryce Harper": "h/harpebr03.shtml",
    "Alec Bohm": "b/bohmal01.shtml",
    "Randy Arozarena": "a/arozara01.shtml",
    "Cal Raleigh": "r/raleica01.shtml",
    "Masyn Winn": "w/winnma01.shtml",
    "Alec Burleson": "b/burleal01.shtml",
    "Paul Goldschmidt": "g/goldspa01.shtml",
    "Nolan Arenado": "a/arenano01.shtml",
    "Ian Happ": "h/happia01.shtml",
    "Seiya Suzuki": "s/suzukse01.shtml",
    "Dansby Swanson": "s/swansda01.shtml",
}

hitters = {
    "Bobby Witt Jr": "w/wittbo02.shtml",
    "Jose Altuve": "a/altuvjo01.shtml",
    "Rafael Devers": "d/deverra01.shtml",
    "Aaron Judge": "j/judgeaa01.shtml",
    "Manny Machado": "m/machama01.shtml",
}


# Headers to include in the requests
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
}

def is_within_past_week(date_str):
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    one_week_ago = datetime.now() - timedelta(days=7)
    return date_obj >= one_week_ago


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
            return None  # Do not scrape data if the date is older than one week
    else:
        return None  # Do not scrape data if the date element is not found

    # Find the table rows within the specified div
    rows = soup.select("#div_last5 > table > tbody > tr")

    at_bats = 0
    hits = 0

    for row in rows[:5]:  # Get only the first 5 rows
        cols = row.find_all("td")
        if len(cols) >= 8:
            col1_value = cols[5].text
            col2_value = cols[7].text

            if col1_value:
                at_bats += int(col1_value)
            if col2_value:
                hits += int(col2_value)

    return {"Player": player, "At Bats": at_bats, "Hits": hits}


def binomial_probability(ab, h):
    # pa = ab + bb
    exp = ab / 5
    avg = h / ab
    return 1 - (1 - avg) ** exp


# Main script to compile data for all players
summary_data = []


for player, url in players.items():
    player_data = scrape_player_data(player, url)
    if player_data:
        player_data["binomial_probability"] = binomial_probability(
            player_data["At Bats"], player_data["Hits"]
        )  # Calculate binomial probability
        summary_data.append(player_data)

    # Sleep between requests
    sleep(random.uniform(1, 5))

# Sort the summary data based on probability in descending order
summary_data.sort(key=lambda x: x["binomial_probability"], reverse=True)

# Print the top n players based on highest binomial probability
n = 10
top_players = summary_data[:n]


# Create and populate the PrettyTable
table = PrettyTable()
table.field_names = ["Player", "ABs", "Hits", "Prob %"]

for data in top_players:
    table.add_row(
        [
            data["Player"],
            data["At Bats"],
            data["Hits"],
            f"{data["binomial_probability"]:.1%}",
        ]
    )

# Print the PrettyTable
print(table)
