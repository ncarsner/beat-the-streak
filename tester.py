import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from prettytable import PrettyTable

# Define the base URL and the dictionary of hitters
site_base = "https://www.baseball-reference.com/players/"
hitters = {
    "Bobby Witt Jr": "w/wittbo02.shtml",
    "Jose Altuve": "a/altuvjo01.shtml",
    "Rafael Devers": "d/deverra01.shtml",
    "Aaron Judge": "j/judgeaa01.shtml",
}

# Function to check if a date is within the past week
def is_within_past_week(date_str):
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    one_week_ago = datetime.now() - timedelta(days=7)
    return date_obj >= one_week_ago

# Function to calculate binomial probability
def binomial_probability(ab: float, h: float, additional_value: float):
    exp = float(ab / 5)
    avg = float(h / ab)
    return (1 - avg) ** exp

# Function to scrape and summarize data for each player
def scrape_player_data(player, url):
    full_url = site_base + url
    response = requests.get(full_url)
    soup = BeautifulSoup(response.content, 'html.parser')

    # Find the date in the specified row and column
    date_elem = soup.select_one('#last5 tr[data-row="4"] th[data-stat="date"]')
    if date_elem:
        date_str = date_elem.text.strip()
        print(f"Debug: Date found for {player}: {date_str}")  # Debug: Print the found date
        if not is_within_past_week(date_str):
            return None  # Do not scrape data if the date is older than one week
    else:
        print(f"Debug: No date found for {player}")  # Debug: Print a message if no date is found
        return None  # Do not scrape data if the date element is not found

    # Find the table rows within the specified table
    rows = soup.select('#last5 > tbody > tr')

    col1_sum = 0
    col2_sum = 0
    additional_sum = 0

    if not rows:
        print(f"Debug: No rows found for {player}")  # Debug: Print a message if no rows are found
        return None

    for row in rows[:5]:  # Get only the first 5 rows
        cols = row.find_all('td')
        if len(cols) >= 7:  # Ensure there are enough columns
            col1_value = cols[5].text
            col2_value = cols[7].text
            additional_value = cols[7].find_next('td').text  # 5 columns to the right of the 2nd column is the 7th column (2+5)

            if col1_value:
                col1_sum += float(col1_value)
            if col2_value:
                col2_sum += float(col2_value)
            if additional_value:
                additional_sum += float(additional_value)
    
    return {
        "player": player,
        "sum_col1": col1_sum,
        "sum_col2": col2_sum,
        "additional_value": additional_sum
    }

# Main script to compile data for all players and calculate probabilities
summary_data = []

for player, url in hitters.items():
    player_data = scrape_player_data(player, url)
    if player_data:
        player_data['binomial_probability'] = binomial_probability(player_data['sum_col1'], player_data['sum_col2'], player_data['additional_value'])
        summary_data.append(player_data)

# Sort the summary data based on binomial probability in descending order
summary_data.sort(key=lambda x: x['binomial_probability'], reverse=True)

# Print the top n players based on highest binomial probability
n = 3  # Number of top players to display
top_players = summary_data[:n]

# Create and populate the PrettyTable
table = PrettyTable()
table.field_names = ["Player", "Sum Col1", "Sum Col2", "Additional Value", "Binomial Probability"]

for data in top_players:
    probability_percentage = f"{data['binomial_probability'] * 100:.1f}%"
    table.add_row([data['player'], data['sum_col1'], data['sum_col2'], data['additional_value'], probability_percentage])

# Print the PrettyTable
print(table)
