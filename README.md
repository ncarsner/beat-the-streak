# Beat the Streak: Analysis Tool

## Description

Beat the Streak is a Python command-line tool that pulls recent per-game batting statistics from the [MLB Stats API](https://statsapi.mlb.com) and ranks hitters by the probability that they will record at least one hit in their next game.

For each player in the configured list the tool:

1. Looks up the player's MLB ID and fetches their regular-season game log for the current year.
2. Takes the **last 5 games** played, provided the most recent of those games fell within the past week.
3. Aggregates at-bats (AB), hits (H), walks (BB), and strikeouts (SO) across those games.
4. Computes a **binomial hit-probability** using the player's recent batting average and plate-appearance rate.
5. Outputs a ranked table showing the top `n` candidates (and the bottom `n` as a contrasting reference).

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

Run against the curated `selected_hitters` subset (default):
```bash
python main.py
```

While running you will see a per-player progress line, for example:
```
[1/10] Fetching Luis Arraez ... ok  (7-18)
[2/10] Fetching Mookie Betts ... skipped (no recent data)
[3/10] Fetching Manny Machado ... ok  (5-16)
...
```

A player is skipped if they can't be found, have no game log for the season, haven't played within the past week, or are still in their no-data cooldown window (see below).

### Controlling the player pool

Use `--mode` to choose which players are fetched and whether the run is capped:

```bash
python main.py --mode subset   # curated selected_hitters list, capped at MAX_PLAYERS (default)
python main.py --mode max      # full player pool, capped at MAX_PLAYERS
python main.py --mode full     # full player pool, uncapped
```

`MAX_PLAYERS` (default `10`) is defined in `main.py` and applies to both `subset` and `max`
modes. Increase it there if you want a bigger validation batch without running the full pool.

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

Team abbreviations come from a static crosswalk in `teams.py`. A player whose current
team isn't yet in the crosswalk (or has no current team at all) shows a blank Team
cell; crosswalk gaps are recorded in `.cache/missing_team_cache.json` for review.

The separator row divides the **top `n×2`** players (best candidates) from the **bottom `n`** players (worst recent performers).

---

## Player list

`players.py` contains the `hitters` dictionary mapping player names to values (player lookups are name-driven against the MLB Stats API, so the dictionary values themselves are not used for fetching). Add or remove players here to customize the pool. The `selected_hitters` list in `main.py` provides a further narrowing filter.

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
