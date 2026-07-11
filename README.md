# Beat the Streak — Analysis Tool

## Description

Beat the Streak is a Python command-line tool that pulls recent per-game batting statistics from the [MLB Stats API](https://statsapi.mlb.com) and ranks hitters by the probability that they will record at least one hit in their next game.

For each player in the configured list the tool:

1. Looks up the player's MLB ID and fetches their regular-season game log for the current year.
2. Takes the **last 5 games** played, provided the most recent of those games fell within the past week.
3. Aggregates at-bats (AB), hits (H), walks (BB), and strikeouts (SO) across those games.
4. Computes a **binomial hit-probability** using the player's recent batting average and plate-appearance rate.
5. Outputs a ranked table showing the top `n` candidates (and the bottom `n` as a contrasting reference).

The metric is intentionally lightweight and designed to complement — not replace — manual lineup review.

---

## Installation

1. Ensure **Python 3.9+** is installed. Get it from [python.org](https://www.python.org/downloads/).

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

No API key or config file is required — the MLB Stats API is public and free to use.

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

A player is skipped if they can't be found, have no game log for the season, or haven't played within the past week.

### Controlling the player pool

`MAX_PLAYERS` (default `10`) in `main.py` caps how many players are fetched per run. This is intentionally conservative so you can validate the tool is working before scaling up.

| Goal | Change |
|------|--------|
| Validate with a small batch | Keep `MAX_PLAYERS = 10` (default) |
| Run the full curated list | Set `MAX_PLAYERS = None` |
| Run all players | Set `MAX_PLAYERS = None` **and** change `selected_hitters` → `hitters` in `__main__` |

### Output format

```
+-------------------------------------------+
|             July 11, 2026                  |
+------------------+-------+------+---------+
| Player           | H-AB  | BB/K | Prob %  |
+------------------+-------+------+---------+
| Luis Arraez      | 8-20  | 3/0  | 90.5%   |
| Manny Machado    | 6-16  | 4/4  | 84.7%   |
| ...              | ...   | ...  | ...     |
+------------------+-------+------+---------+
| ---              | ---   | ---  | ---     |
+------------------+-------+------+---------+
| TJ Friedl        | 1-14  | 1/6  | 19.9%   |
| ...              | ...   | ...  | ...     |
+------------------+-------+------+---------+
```

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
