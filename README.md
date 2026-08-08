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
7. Looks up the batter's head-to-head history against the opposing probable starter (see [Batter-vs-pitcher calculators](#batter-vs-pitcher-calculators)).
8. Outputs a ranked table showing the top `n` candidates (and the bottom `n` as a contrasting reference).

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

### SMS notifications

When running in `--scheduled` mode, the tool sends an SMS via Twilio for each game-start-time
grouping (keyed by `GameHourUTC`) that has qualifying players. Each message lists up to the top
5 players for that window, ranked by hit probability.

**Behavior:**
- SMS sending only occurs in `--scheduled` mode; the default manual mode never sends a text.
- Each grouping is texted at most once per calendar day. A confirmed 2xx response marks the
  grouping as sent; a failed or non-2xx response leaves it unmarked so the next `--scheduled`
  run within the same window retries automatically.
- If any of the four required environment variables is unset, the SMS step is logged and skipped
  without crashing the run or blocking table output.

**Required environment variables:**

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_NUMBER` | Sending phone number in E.164 format (e.g. `+15551234567`) |
| `SUBSCRIBER_PHONE_NUMBER` | Recipient phone number in E.164 format |

**GitHub Actions workflow** (`.github/workflows/sms-notify.yml`):

The workflow runs `python3 main.py --scheduled` on a 15-minute cron (`*/15 14-23,0-2 * * *`),
covering 10am–10pm EDT. The job is gated by the `SEASON_ACTIVE` repository variable — set it to
any non-empty value in **Settings > Variables** to enable live runs; clear it during the
off-season.

The four Twilio credentials must be added as repository secrets in
**Settings > Secrets and variables > Actions**. The `.cache/` directory is persisted across
same-day runs via `actions/cache` (keyed by date), so the SMS-sent cache survives between the
15-minute firings.

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

### Batter-vs-pitcher calculators

`calculators.py` implements the Category 1 (batter-vs-pitcher, "BvP") considerations from
`ROADMAP.md`, using the MLB Stats API's `vsPlayer` stat type. One request per batter covers
all four — it returns a split for every season the pair has faced each other, plus a career
total:

| ID | Calculator | Definition |
|---|---|---|
| `CALC_01` | BvP Career Hit Rate | H / PA across all head-to-head plate appearances |
| `CALC_02` | BvP Season Hit Rate | H / PA head-to-head in the current season |
| `CALC_03` | BvP Recent Window Hit Rate | H / PA head-to-head over the last 3 calendar years |
| `CALC_04` | BvP Contact Rate | (PA − SO − BB) / PA head-to-head |

Each returns a `BvPRate` — the rate paired with the plate appearances behind it — or `None`
when the pair has never faced each other, when no starter has been announced, or when the
game begins with an unlisted opener. **These values are displayed only; they do not affect
the ranking.** BvP samples are small (a 1-for-2 career line is not evidence of a .500 hitter),
so weighting them belongs in the Bayesian composite (`CALC_75`), which is not yet implemented.

`CALC_05`–`CALC_08` (hard-hit %, xBA/xwOBA, whiff rate, putaway rate) require Statcast
pitch-level data that the MLB Stats API does not expose per batter-pitcher pair, and are not
implemented.

Career totals are summed from the per-season splits rather than read from the API's own
`vsPlayerTotal` line, which has been observed to disagree with them.

### Output format

```
+----------------------------------------------------------------------+
|                            July 11, 2026                             |
+------------------+------+-------+------+---------+-----------+--------+
| Player           | Team | H-AB  | BB/K | Prob %  | BvP Car   | BvP 3Y |
+------------------+------+-------+------+---------+-----------+--------+
| Luis Arraez      | SD   | 8-20  | 3/0  | 90.5%   | .316 (19) | .400 (5)|
| Manny Machado    | SD   | 6-16  | 4/4  | 84.7%   | -         | -      |
| ...              | ...  | ...   | ...  | ...     | ...       | ...    |
+------------------+------+-------+------+---------+-----------+--------+
| ---              | ---  | ---   | ---  | ---     | ---       | ---    |
+------------------+------+-------+------+---------+-----------+--------+
| TJ Friedl        | CIN  | 1-14  | 1/6  | 19.9%   | .000 (3)  | .000 (3)|
| ...              | ...  | ...   | ...  | ...     | ...       | ...    |
+------------------+------+-------+------+---------+-----------+--------+
```

`BvP Car` (`CALC_01`) and `BvP 3Y` (`CALC_03`) show the head-to-head rate followed by the
plate appearances it was computed over; `-` means no shared history or no announced starter.

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
