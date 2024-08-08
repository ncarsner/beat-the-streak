# Beat the Streak analysis tool 


## Description
This is a Python-based tool that compiles hitter data from `Baseball-Reference.com` player sites and calculates a metric using binomial distribution probability based on batting average and plate appearances over the previous week. The script evaluates the top and bottom `n` candidates for the current date based on performance over their previous five (5) games played.

## Installation
To set up Beat the Streak, follow these steps:

1. Make sure you have Python installed. You can download it from [python.org](https://www.python.org/downloads/).

2. Clone the repository:
`git clone https://github.com/ncarsner/beat-the-streak.git`

3. Navigate to the project directory:
`cd beat-the-streak`

4. Create a virtual environment, i.e `python3 -m venv <venv>`

5. Install the required dependencies by running: `pip install -r requirements.txt`


## Usage
To execute the game, run: `python main.py`

## Development Notes
This tool is designed with the intent to scale execution in conjunction with scheduled games and published lineups so as not to abuse source data provider.

## Contributing
Contributions to Beat the Streak are welcome.<br>
Please ensure to update tests as appropriate.

## License
[MIT](https://choosealicense.com/licenses/mit/)
