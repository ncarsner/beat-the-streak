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

3. Install dependencies. This project uses [**uv**](https://docs.astral.sh/uv/):
   ```bash
   uv sync
   ```

   Or with pip, if you prefer:
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   Either includes **pybaseball**, which the Statcast calculators need and which pulls a
   large transitive tree (pandas, numpy, altair, cryptography). Nothing in the daily run
   imports it — if you only want to run the ranking tool, `requests` and `prettytable` are
   enough. The scheduled GitHub Actions workflow installs `requests` only.

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
    __init__.py                          # public surface
    common.py                            # Window, roles, Rate, counting-stat helpers
    baselines.py                         # loaders for generated league-reference data
    data/                                # generated reference data, checked in
    category_01_bvp_matchups.py          # CALC_01-08
    category_02_platoon_splits.py        # CALC_09-15
    category_06_lineup_game_context.py   # CALC_41
    sources/                             # the only package here that touches the network
        common.py                        #   HTTP + Statcast response cache
        category_01_bvp_matchups.py
        category_02_platoon_splits.py
tests/                                   # mirrors the module layout, one file per module
scripts/                                 # on-demand generators, never run by the tool
```

`MLB_API_BASE` lives in `mlb_api.py`, shared by `main.py` and the source modules.

The convention, applied to each category as it lands:

| Thing | Pattern | Example |
|---|---|---|
| Module | `category_NN_<slug>.py` | `category_01_bvp_matchups.py` |
| Calculator | `calc_NN_<slug>(...)` | `calc_01_bvp_career_hit_rate` |
| Category aggregate | `compute_category_NN(...)` | `compute_category_01` |
| Fetch (in `sources/`) | `fetch_<subject>(...)` | `fetch_bvp_stats` |
| Test module | `tests/test_<module>.py` | `tests/test_category_01_bvp_matchups.py` |

Pure calculator modules never import `sources`, which is what keeps the arithmetic testable
without mocking a request.

#### The cross-category contract

Two shared types in `common.py` keep eleven categories speaking the same language.

**`Window`** — the temporal scope a calculator operates over, applied as a local slice over
one season-wide fetch rather than as a separate request per window:

| Window | Meaning |
|---|---|
| `CAREER()` | everything available |
| `SEASON()` | the anchor season only |
| `DAYS(n)` | the `n` calendar days **ending yesterday** — today's game has not been played |
| `GAMES(n)` | the last `n` distinct games, appearance-anchored |
| `PLATE_APPEARANCES(n)` | the last `n` PAs, appearance-anchored |
| `SEASONS(n)` | the last `n` seasons, inclusive of the anchor |

`DAYS(n)` is calendar-anchored on purpose. A hitter returning from a 12-day injured-list stint
gets a 14-day window containing one game, and the `Rate`'s denominator says so — rather than
reaching back five weeks to manufacture a full sample that would look current and not be.
An empty window returns `None`: absence of evidence, never a 0.0 rate.

**Role** — what the composite model is allowed to do with a value. Every calculator tags its
own return, so `CALC_75` dispatches on the tag instead of carrying a lookup table of what each
of 76 numbers means:

| Role | Meaning |
|---|---|
| `PROBABILITY` | blends into `p_hit` |
| `MULTIPLIER` | scales `p_hit`; **not** a probability |
| `EXPONENT` | feeds `PA_proj`, the exponent — not `p_hit` |
| `DELTA` | signed adjustment; may be negative, not clamped to [0, 1] |

This is what makes adding a park factor to a hit rate a type error rather than a
plausible-looking number.

Every value carries a `Rate` — the number **and** the sample size it came from. No calculator
returns a bare float, because a 1-for-2 head-to-head line is not evidence of a .500 hitter,
and the composite needs the count to shrink small samples toward a prior.

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
| `CALC_05` | BvP Hard Hit % | batted balls ≥ 95 mph exit velocity / batted balls |
| `CALC_06` | BvP xBA / xwOBA | mean expected BA and wOBA on contact (two keys) |
| `CALC_07` | BvP Whiff Rate | swings and misses / total swings |
| `CALC_08` | BvP Putaway Rate | strikeouts / pitches in two-strike counts |

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

`CALC_01`–`CALC_04` come from the MLB Stats API. `CALC_05`–`CALC_08` need Statcast pitch-level
data, which that API does not expose per batter-pitcher pair, so they come from Baseball Savant
via **pybaseball** (`fetch_bvp_statcast`). Two consequences worth knowing:

- **They are season-scoped, not career-scoped.** Statcast starts in 2015 and `statcast_batter`
  pulls one season per call, so `CALC_05`–`CALC_08` describe a different span of time than
  `CALC_01`'s true career window. The season is a parameter on the fetch.
- **`CALC_06` is emitted under two keys**, `CALC_06_XBA` and `CALC_06_XWOBA` — the one ROADMAP
  consideration carrying two metrics.

Swing and whiff classification for `CALC_07` is by Statcast `description`, listed exactly in
`SWING_DESCRIPTIONS` / `WHIFF_DESCRIPTIONS`. A tipped ball counts as a swing but not a miss,
since ROADMAP defines the calculator as "swings and misses".

pybaseball is imported lazily inside the fetch function, never at module scope: it pulls in
pandas and a large transitive tree, and neither the daily run nor most of the test suite needs
it. The scheduled workflow installs `requests` only and does not read `requirements.txt`, so
the cron is unaffected.

The API's two groups are independently unreliable, so career totals are summed from the
per-season splits and the API's own `vsPlayerTotal` line is used only as a fallback. Both
failure modes have been observed live on the same batter-pitcher pair minutes apart: a
`vsPlayerTotal` of 2 PA against a season split of 3 PA, and an empty season-split list
alongside a populated 3 PA total. Deriving career from the splits keeps `CALC_01`'s sample
from ever being smaller than `CALC_03`'s window over the same matchup; when no splits come
back at all, `CALC_02` and `CALC_03` are `None` while `CALC_01` still reports the total.

### Platoon and handedness calculators

`category_02_platoon_splits.py` implements Category 2 (`CALC_09`–`CALC_15`). Handedness comes
from one batched `GET /people?personIds=...` covering every batter and probable starter in the
run — **not** from the lineup or schedule payloads, which do not carry `batSide` or `pitchHand`
at all, with or without `hydrate=probablePitcher(person)`.

| ID | Calculator | Source | Role |
|---|---|---|---|
| `CALC_09` | Hitter season vs. pitcher throws | `statSplits` | `PROBABILITY` |
| `CALC_10` | Hitter recent (14d) vs. pitcher throws | Statcast | `PROBABILITY` (+ xBA key) |
| `CALC_11` | Pitcher season vs. batter bats | `statSplits` | `PROBABILITY` |
| `CALC_12` | Pitcher recent (14d) vs. batter bats | Statcast | `PROBABILITY` (+ xBA key) |
| `CALC_13` | Switch-hitter split acuity | `statSplits` | `DELTA` |
| `CALC_14` | Arm slot / release angle match | Statcast | `PROBABILITY` |
| `CALC_15` | Reverse platoon split index | `statSplits` + league 2×2 | `MULTIPLIER` |

The two 14-day calculators come from Statcast rather than the Stats API because **`sitCodes`
and date ranges do not compose** there: `byDateRange` honors the window and silently drops the
split, while `statSplits` honors the split and silently ignores the window. There is no
Stats-API path to a date-windowed handedness split.

Rates are **per plate appearance**, not per at-bat. The ROADMAP words several of these as "BA",
but `p_hit` is defined per PA and combined as `1 − (1 − p_hit)^PA_proj`; an H/AB rate fed into a
per-PA exponent overstates by roughly ten percent, since walks leave the AB denominator.

`CALC_13` and `CALC_15` carry the **limiting** side's denominator — a differential is only as
trustworthy as its thinner half, and for switch hitters that half is the off-side sample a
platoon-savvy manager spends all season avoiding.

`CALC_14` buckets arm angle at **30°** and **42°**, tertile-balanced against a measured sample
of 233 pitchers (median 37°, min −61° submarine, max 69° — nobody throws from 90°). The
conventional 20°/45° boundaries were rejected: they put 70% of the league in one bucket, which
would have `CALC_14` measuring a hitter against nearly everyone.

### League platoon baseline

`CALC_15` (reverse platoon split index) measures a hitter's own platoon gap against the
direction expected for their handedness, which needs a league baseline conditioned on
**both** hands. `calculators/data/league_platoon_baseline.json` holds that 2×2, generated
on demand rather than fetched per run:

```bash
uv run python -m scripts.generate_league_platoon_baseline
```

Four requests, a few seconds. The pooled vs-L / vs-R figures cannot substitute: aggregated
across all hitters they come out nearly identical (.2423 and .2444), because left- and
right-handed batters have opposite platoon advantages that cancel. Conditioned on batter
hand the effect is plain — each hand hits roughly 10–17 points better against the opposite
hand. Team-level aggregates cannot produce this, since teams are not split by batter hand.

Switch hitters are excluded; they have no fixed batter hand, and `CALC_13` handles them.

### Lineup spot and CALC_41

`fetch_lineup` carries each player's `lineup_spot`, decoded from the boxscore's 3-digit
`battingOrder` encoding (`"100"` = spot 1, `"101"` = the first substitute batting there, so
`int(v) // 100` recovers the spot). `CALC_41` projects plate appearances from it across the
ROADMAP's 4.6-to-3.7 range, with role `EXPONENT` — it feeds `PA_proj`, never `p_hit`.

**Nothing in `calculators/` is called during a run.** No BvP, handedness, or Statcast request
is made, `binomial_probability` still computes its exponent as `pa / 5`, and the ranked table
is unchanged. Calculators are validated in isolation and will be consumed together by the
composite model (`CALC_75`).

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
uv run pytest tests/ -v
```

The suite is offline by design and never reaches a live API: every fetcher is monkeypatched
and every payload is a fixture. An autouse guard in `tests/conftest.py` enforces this by
blocking non-loopback socket connections, so a test that forgets to patch a fetcher fails
loudly instead of quietly scraping Baseball Savant on every run.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## Contributing

Contributions are welcome. Please update the appropriate module under `tests/` when adding features.

## License

[MIT](https://choosealicense.com/licenses/mit/)
