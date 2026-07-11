# Beat the Streak — Analysis Tool

## Description

Beat the Streak is a Python command-line tool that scrapes per-game batting statistics from [Baseball-Reference.com](https://www.baseball-reference.com) and ranks hitters by the probability that they will record at least one hit in their next game.

For each player in the configured list the tool:

1. Fetches the player's **last 5 games** played within the past week from the `#div_last5` section of their Baseball-Reference profile page.
2. Aggregates at-bats (AB), hits (H), walks (BB), and strikeouts (SO) across those games.
3. Computes a **binomial hit-probability** using the player's recent batting average and plate-appearance rate.
4. Outputs a ranked table showing the top `n` candidates (and the bottom `n` as a contrasting reference).

The metric is intentionally lightweight and designed to complement — not replace — manual lineup review.

---

## Installation

1. Ensure **Python 3.9+** is installed.  Get it from [python.org](https://www.python.org/downloads/).

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

5. (Optional) Copy the config template and customise the User-Agent header used for HTTP requests:
   ```bash
   cp config.ini.example config.ini
   # Edit config.ini with your preferred User-Agent string
   ```
   If `config.ini` is absent the tool falls back to a sensible default User-Agent automatically.

---

## Usage

Run against the curated `selected_hitters` subset (default):
```bash
python main.py
```

While running you will see a per-player progress line, for example:
```
[1/10] Fetching Luis Arraez ...  ok  (7-18)
[2/10] Fetching Jurickson Profar ...  ok  (5-16)
[3/10] Fetching Manny Machado ...  skipped (no recent data)
...
```

If the server starts rejecting requests (HTTP 403 / 429), the run stops immediately with a message and displays results collected so far:
```
[!] HTTP 429 — server is blocking requests. Stopping further requests.
```

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
|           April 20, 2025                  |
+------------------+-------+------+---------+
| Player           | H-AB  | BB/K | Prob %  |
+------------------+-------+------+---------+
| Steven Kwan      | 7-18  | 3/2  | 84.3%   |
| Luis Arraez      | 6-17  | 4/1  | 82.1%   |
| ...              | ...   | ...  | ...     |
+------------------+-------+------+---------+
| ---              | ---   | ---  | ---     |
+------------------+-------+------+---------+
| Jake Burger      | 1-16  | 0/7  | 28.4%   |
| ...              | ...   | ...  | ...     |
+------------------+-------+------+---------+
```

The separator row divides the **top `n×2`** players (best candidates) from the **bottom `n`** players (worst recent performers).

---

## Player list

`players.py` contains the `hitters` dictionary mapping player names to their Baseball-Reference URL slugs.  Add or remove players here to customise the pool.  The `selected_hitters` list in `main.py` provides a further narrowing filter.

---

## How the probability is calculated

```
PA  = AB + BB
exp = PA / 5          # estimated plate appearances per game
avg = H / AB          # recent batting average
P   = 1 − (1 − avg)^exp
```

`P` is the probability of recording **at least one hit** in a hypothetical game with `exp` plate appearances, given the player's recent average.  Players with zero at-bats in the sample window are excluded.

---

## Running tests

```bash
pytest test_suite.py -v
```

---

## Changes in this update

The following bugs were identified and fixed after the tool stopped returning data:

| # | Issue | Fix |
|---|-------|-----|
| 1 | **Missing `config.ini` caused an immediate `KeyError` crash** — the file is `.gitignore`d but was required at import time | Made config optional; falls back to a bundled default User-Agent |
| 2 | **`headers=None` passed to `requests.get`** — HTTP headers were loaded from config but never forwarded, causing Baseball-Reference to reject requests | Passed the loaded `headers` dict to every request |
| 3 | **Baseball-Reference wraps secondary tables in HTML comments** for lazy-loading — `BeautifulSoup` skips comment content by default, so `#div_last5` was never found | Added `_find_last5_div()` which searches inside `Comment` nodes as a fallback |
| 4 | **Fragile positional column indices** (`cols[5]`, `cols[7]`, etc.) broke silently whenever Baseball-Reference reorganised their table columns | Replaced with `data-stat` attribute lookups (`AB`, `H`, `BB`, `SO`) which are stable across layout changes |
| 5 | **Date detection relied on the hardcoded 5th-row CSS selector** — failed if the player had played fewer than 5 games or the table structure changed | Now iterates rows in reverse and uses `th[data-stat="date_game"]` to find the most-recent game date; handles doubleheader date suffixes |
| 6 | **`ZeroDivisionError`** when a player had zero at-bats in the sample window | Added an `ab == 0` guard in `binomial_probability` and an `At Bats > 0` check in `compile_player_data` |
| 7 | **Windows-only `%#d` strftime format** in `probable_hitters` raised `ValueError` on Linux/macOS | Replaced with `today.day` integer formatting |
| 8 | **Nested f-string quoting** (`f"{data["key"]}"`) required Python 3.12+ | Changed inner keys to single-quoted (`f"{data['key']}"`) for broad compatibility |
| 9 | **Test suite had multiple failures** — wrong `config.ini` dependency at import, stale 2024 date in mock, incorrect column counts, extra `headers` kwarg, wrong expected values for `binomial_probability` | Rewrote tests to be self-contained, use a dynamic recent date, and match the current formula |

---

## Contributing

Contributions are welcome.  Please update `test_suite.py` as appropriate when adding features.

## License

[MIT](https://choosealicense.com/licenses/mit/)

