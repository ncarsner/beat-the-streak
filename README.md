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

`MAX_PLAYERS` (default `50`) in `main.py` caps the total number of players processed per run
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

Calculators live in the `calculators/` package, one module per `ROADMAP.md` category, so a
calculator sits with the others that share its data source:

```
calculators/
    __init__.py                    # public surface
    common.py                      # Rate + counting-stat helpers shared by all categories
    category_01_bvp_matchups.py    # CALC_01-08
    sources.py                     # the only module here that touches the network
```

`MLB_API_BASE` lives in `mlb_api.py`, shared by `main.py` and `calculators/sources.py`.

The convention, applied to each category as it lands:

| Thing | Pattern | Example |
|---|---|---|
| Module | `category_NN_<slug>.py` | `category_01_bvp_matchups.py` |
| Calculator | `calc_NN_<slug>(...)` | `calc_01_bvp_career_hit_rate` |
| Category aggregate | `compute_category_NN(...)` | `compute_category_01` |
| Fetch (in `sources.py`) | `fetch_<subject>(...)` | `fetch_bvp_stats` |

Pure calculator modules never import `sources`, which is what keeps the arithmetic testable
without mocking a request.

`category_01_bvp_matchups.py` implements the Category 1 (batter-vs-pitcher, "BvP")
considerations, using the MLB Stats API's `vsPlayer` stat type. One request per batter covers
all four — it returns a split for every season the pair has faced each other, plus a career
total:

| ID | Calculator | Definition |
|---|---|---|
| `CALC_01` | BvP Career Hit Rate | H / PA across all head-to-head plate appearances |
| `CALC_02` | BvP Season Hit Rate | H / PA head-to-head in the current season |
| `CALC_03` | BvP Recent Window Hit Rate | H / PA head-to-head over the last 3 calendar years |
| `CALC_04` | BvP Contact Rate | (PA − SO − BB) / PA head-to-head |

Each returns a `Rate` — the value paired with the sample size behind it — or `None` when the
pair has never faced each other, when no starter has been announced, or when the game begins
with an unlisted opener.

**These calculators are not part of the daily run yet.** Nothing calls them, they do not appear
in the output table, and they do not affect the ranking — no BvP request is made during a run.
They are built and tested ahead of the composite model (`CALC_75`), which is where a BvP rate
gets shrunk toward a prior before it can influence anything; a 1-for-2 career line is not
evidence of a .500 hitter. `calculators/sources.py` holds the I/O half ready for that release:
`fetch_bvp_stats` (one request per batter, covering all four) and `attach_category_01`.
`main.py` imports nothing from the package.

Supporting groundwork *is* live, since it costs no extra requests: `fetch_schedule` hydrates
`probablePitcher`, and each lineup entry carries the `opposing_pitcher_id` of the other side's
announced starter.

`CALC_05`–`CALC_08` (hard-hit %, xBA/xwOBA, whiff rate, putaway rate) require Statcast
pitch-level data that the MLB Stats API does not expose per batter-pitcher pair, and are not
implemented.

The API's two groups are independently unreliable, so career totals are summed from the
per-season splits and the API's own `vsPlayerTotal` line is used only as a fallback. Both
failure modes have been observed live on the same batter-pitcher pair minutes apart: a
`vsPlayerTotal` of 2 PA against a season split of 3 PA, and an empty season-split list
alongside a populated 3 PA total. Deriving career from the splits keeps `CALC_01`'s sample
from ever being smaller than `CALC_03`'s window over the same matchup; when no splits come
back at all, `CALC_02` and `CALC_03` are `None` while `CALC_01` still reports the total.

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
