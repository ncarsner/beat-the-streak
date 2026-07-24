# Beat the Streak: Analysis Tool

## Description

Beat the Streak is a Python command-line tool that pulls posted MLB lineups and recent per-game batting statistics from the [MLB Stats API](https://statsapi.mlb.com), then ranks hitters by the probability that they will record at least one hit in their next game.

For each player in the day's posted lineup the tool:

1. Fetches the day's schedule and selects games based on the chosen window mode (manual or scheduled).
2. Retrieves the posted batting-order lineup from the boxscore API for each selected game.
3. For each player in the lineup, fetches their regular-season game log for the current year.
4. Takes the **last 5 games** played, provided the most recent of those games fell within the past week.
5. Aggregates at-bats (AB), hits (H), walks (BB), and strikeouts (SO) across those games.
6. Computes a **binomial hit-probability** using the player's recent batting average and plate-appearance rate.
7. Outputs a ranked table showing the top `n` candidates (and the bottom `n` as a contrasting reference).

The metric is intentionally lightweight and designed to complement, not replace, manual lineup review.

---

## Installation

1. Ensure **Python 3.10+** is installed. Get it from [python.org](https://www.python.org/downloads/).

2. Clone the repository:
   ```bash
   git clone https://github.com/ncarsner/beat-the-streak.git
   cd beat-the-streak
   ```

3. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

No API key or config file is required: the MLB Stats API is public and free to use.

---

## Usage

Run in manual mode (all games from now through end of day):
```bash
python main.py
```

Run in scheduled/cron mode (only games starting within the next 2 hours):
```bash
python main.py --scheduled
```

While running you will see a per-player progress line, for example:
```
[1/18] Fetching Luis Arraez ... ok  (7-18)
[2/18] Fetching Mookie Betts ... skipped (no recent data)
[3/18] Fetching Manny Machado ... ok  (5-16)
...
```

A player is skipped if they have no game log for the season, haven't played within the past week, or are still in their no-data cooldown window (see below). Games whose lineups have already been queried today are skipped on subsequent runs automatically.

### Window selection and --scheduled

| Flag | Games included |
|------|---------------|
| *(default, manual mode)* | All games with a start time at or after now |
| `--scheduled` | Only games starting within the next 2 hours |

`--scheduled` is designed for cron use (e.g. every 15–20 minutes): it restricts the run to
imminent games so the tool only fires when lineups are actually relevant. Manual mode is
useful for ad-hoc runs where you want to see all games still to be played today.

`MAX_PLAYERS` (default `10`) in `main.py` caps the total number of players processed per run
after window and lineup selection.

### No-data cache and cooldown

A player who polls with no recent data is remembered in `.cache/no_data_cache.json` and
skipped on later runs for a cooldown period, since a fresh game log won't have accumulated
in less time than that. The player is automatically rechecked once the cooldown elapses, and
cleared from the cache as soon as they produce data again.

The cooldown defaults to 7 days and can be adjusted per run:

```bash
python main.py --cooldown-days 3
```

Use `--cooldown-days 0` to disable the cooldown and recheck every player on every run.

### Output format

```
+----------------------------------------------------+
|                  July 11, 2026                      |
+------------------+------+-------+------+---------+
| Player           | Team | H-AB  | BB/K | Prob %  |
+------------------+------+-------+------+---------+
| Luis Arraez      | SD   | 8-20  | 3/0  | 90.5%   |
| Manny Machado    | SD   | 6-16  | 4/4  | 84.7%   |
| ...              | ...  | ...   | ...  | ...     |
+------------------+------+-------+------+---------+
| ---              | ---  | ---   | ---  | ---     |
+------------------+------+-------+------+---------+
| TJ Friedl        | CIN  | 1-14  | 1/6  | 19.9%   |
| ...              | ...  | ...   | ...  | ...     |
+------------------+------+-------+------+---------+
```

Team abbreviations are resolved by numeric team ID via `TEAM_ID_TO_ABBR` in `teams.py`.
Since team IDs come directly from the lineup data, no name-matching or gap-tracking file
is needed.

The separator row divides the **top `n×2`** players (best candidates) from the **bottom `n`** players (worst recent performers).

---

## How the probability is calculated

```
PA  = AB + BB
exp = PA / 5          # estimated plate appearances per game
avg = H / AB          # recent batting average
P   = 1 − (1 − avg)^exp
```

`P` is the probability of recording **at least one hit** in a hypothetical game with `exp` plate appearances, given the player's recent average. Players with zero at-bats in the sample window are excluded.

---

## Running tests

```bash
pytest test_suite.py -v
```

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## Contributing

Contributions are welcome. Please update `test_suite.py` as appropriate when adding features.

## License

[MIT](https://choosealicense.com/licenses/mit/)
