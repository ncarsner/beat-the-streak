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

### Model calculators

Calculators live in the `calculators/` package, one module per `ROADMAP.md` category, so a
calculator sits with the others that share its data source. Most are batter-versus-pitcher
matchups; Categories 8 and 9 are single-sided, reporting one player's recent form.

```
calculators/
    __init__.py                          # public surface
    common.py                            # Window, roles, Rate, and the shared Statcast vocabulary
    baselines.py                         # loaders for generated league-reference data
    data/                                # generated reference data, checked in
    category_01_bvp_matchups.py          # CALC_01-08
    category_02_platoon_splits.py        # CALC_09-15
    category_03_pitch_arsenal.py         # CALC_16-23
    category_04_plate_discipline.py      # CALC_24-30
    category_05_ballpark_environment.py  # CALC_31-40
    category_06_lineup_game_context.py   # CALC_41-46
    category_08_batter_form.py           # CALC_52-59
    category_09_pitcher_form.py          # CALC_60-65
    sources/                             # the only package here that touches the network
        common.py                        #   HTTP + Statcast response cache
        category_01_bvp_matchups.py
        category_02_platoon_splits.py
        category_03_pitch_arsenal.py
        category_04_plate_discipline.py
        category_05_ballpark_environment.py
        category_06_lineup_game_context.py
        category_08_batter_form.py
        category_09_pitcher_form.py
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

### Pitch arsenal and movement calculators

`category_03_pitch_arsenal.py` implements Category 3 (`CALC_16`–`CALC_23`), entirely from
Statcast pitch-level columns. Every one of them takes **both** sides of the matchup: the
starter's rows establish what he throws, the hitter's rows how he fares against that class
of pitch. The hitter's side is deliberately not restricted to this starter — Category 1
already owns the head-to-head question and returns nothing when the pair has never met, so
Category 3 is what still has a sample against a rookie.

| Calculator | Primary key | Secondary key |
|---|---|---|
| `CALC_16`/`17`/`18` | hitter xBA on fastballs / breaking / offspeed | `_USAGE`: the starter's share of that class |
| `CALC_19` | run value per 100 pitches on the starter's top-2 types (`DELTA`, in runs) | — |
| `CALC_20` | hitter H/PA in the starter's fastball velocity bracket | — |
| `CALC_21` | hitter H/PA against the starter's fastball approach-angle tier | `_PLANE_MISMATCH`: swing plane vs. pitch plane, in degrees |
| `CALC_22` | hitter H/PA against extreme horizontal break | `_USAGE`: the starter's share of it |
| `CALC_23` | hitter H/PA in the starter's release-extension tier | `_VELO_GAIN`: mph that extension buys, `DELTA` |

Three decisions worth knowing before reading the numbers.

**Two keys, not their product.** The ROADMAP writes `CALC_16` as "hitter xBA × pitcher usage
%", but that product is not a quantity anything can read — a .300 xBA against a starter who
throws 55% fastballs multiplies to .165, which is neither an expected average nor a usage.
Both terms survive and `CALC_75` weights them. The three usage shares divide by *typed*
pitches rather than by their own sum, so they come to slightly under 1 and the unclassified
residue (pitchouts, intentional balls, the odd eephus — about 0.1% of league pitches) stays
visible instead of being silently redistributed.

**Terminal-pitch attribution.** Category 2 filters on attributes that hold constant across a
plate appearance, so it can keep every pitch of one. Category 3 filters on attributes that
change pitch to pitch — a PA can see a 96 mph fastball and an 84 mph slider — so each PA is
reduced to the pitch that *ended* it before any tier filter runs. Verified against Statcast:
the maximum-`pitch_number` row of a PA is exactly the row carrying a terminal `events` value,
240 of 240 in the probe sample.

**`CALC_21` does not take its value from swing plane.** Statcast's bat-tracking `attack_angle`
measures it directly and would be the obvious primary, but the column is entirely null before
2024 and populated only on pitches the batter swung at. A calculator depending on it returns
`None` across most of any backtest, which is exactly what makes it useless to the validation
harness. Swing plane is emitted as the secondary `CALC_21_PLANE_MISMATCH` in signed degrees
(zero means the swing parallels the incoming pitch), and the primary is a hit rate on the
same probability scale as everything else.

Tier boundaries are measured rather than assumed, against 20,545 pitches seen by 12 regular
hitters over the 2026 season. Vertical approach angle is solved from the release-point
velocity and acceleration vectors — Statcast publishes the trajectory terms but not the
angle — and the solution's sign was validated by the ordering it produces: four-seamers
flattest at −4.63°, then sinkers, cutters, the breaking balls, and curveballs steepest at
−9.52°. It is tiered over **fastballs only**, because pooled across pitch types VAA mostly
measures arsenal mix rather than delivery.

`pfx_x` is in feet; Baseball Savant displays the same quantity in inches. The constant is
named `EXTREME_PFX_X_FEET` so the 12× error has somewhere to fail loudly.

### Plate discipline and zone location calculators

`category_04_plate_discipline.py` implements Category 4 (`CALC_24`-`CALC_30`), also entirely
from Statcast pitch-level columns and also two-sided. Every calculator emits two keys, the
hitter's rate and the pitcher rate that pairs with it, for the same reason Category 3 does.

| Calculator | Hitter key | Pitcher key |
|---|---|---|
| `CALC_24` | zone-contact rate, contact per in-zone swing | `_ZONE_RATE`: share of tracked pitches in the zone |
| `CALC_25` | chase rate, swings per out-of-zone pitch seen | `_OZONE_RATE`: share of tracked pitches out of the zone |
| `CALC_26` | whiff rate, per swing | `_SWSTR`: swinging strikes, per pitch |
| `CALC_27` | called-strike-plus-whiff rate allowed, per pitch | `_PITCHER_CSW`: the same rate generated |
| `CALC_28` | 0-0 swing rate | `_F_STRIKE`: first-pitch strike rate |
| `CALC_29` | two-strike contact rate, per swing | `_PITCHER_OUT`: outs per plate appearance reaching two strikes |
| `CALC_30` | zone xBA weighted by where the starter works (`PROBABILITY`) | `_COVERAGE`: share of his in-zone pitches the hitter has a sample for |

Four things to know before reading the numbers.

**Almost everything here is tagged `MULTIPLIER`, and that tag is narrower than it looks.**
These are skill rates, not batting averages: a .28 chase rate, a .25 whiff rate, and an .85
zone-contact rate live on three different scales, and two of the three are bad news for the
hitter while the third is good. `MULTIPLIER` is the available role meaning "not p_hit-scale,
do not blend directly", which is the property that matters at the call site. Nothing should
multiply `p_hit` by one of these until issue #39 settles the mapping. `CALC_30` is the
category's only `PROBABILITY`, because a location-weighted xBA genuinely is on that scale.

**Zone comes from Statcast's own `zone` column, not from coordinates.** Codes 1-9 are the
3x3 in-zone grid `CALC_30` asks for and 11-14 the out-of-zone quadrants (there is no 10).
Reconstructing in-zone from `plate_x` against half the plate width disagreed with the
published zone on 4.3% of tracked pitches, because the published version accounts for the
ball's radius and a per-batter zone. `plate_x`, `plate_z`, `sz_top`, and `sz_bot` are
deliberately absent from the field tuple.

**`CALC_29`'s out rate enumerates outcomes in both directions.** A plate appearance whose
terminal event is in neither `OUT_EVENTS` nor `ON_BASE_EVENTS` is dropped from numerator and
denominator, rather than falling through a "not on base means out" complement. Statcast ends
plate appearances on `truncated_pa` and `caught_stealing_2b`, and a complement rule would
score every one of those as a pitcher success. That is the same silent-negative shape as the
`CALC_14` defect in issue #37, which is also why `CALC_29`'s pitcher side reduces to the
terminal pitch before filtering on the count.

**Two denominators that look wrong and are not.** `CALC_26` is per swing on the hitter's side
and per pitch on the pitcher's, which is the ROADMAP's definition and the industry
convention: swinging-strike rate credits a pitcher for provoking the swing at all. And
`CALC_29`'s two-strike contact rate runs *above* the same hitter's overall contact rate
(.764 against .694 on the smoke-test hitter) because every foul with two strikes keeps the
plate appearance alive at two strikes, so a hitter who battles contributes many contacts and
no whiffs.

### Batter form and quality-of-contact calculators

`category_08_batter_form.py` implements Category 8 (`CALC_52`-`CALC_59`), the first
**single-sided** category: it asks only how the hitter is going, so nothing about today's
starter enters. It is also the first real consumer of `common.py`'s `Window` machinery.

| Calculator | Value | Window |
|---|---|---|
| `CALC_52` | hits per plate appearance, plus `_MULTI_HIT` (share of games with 2+) | last 3 games |
| `CALC_53` / `CALC_54` | hits per plate appearance | last 7 / 14 calendar days |
| `CALC_55` | `_XWOBA` and `_HARD_HIT`, both per batted ball | last 14 days |
| `CALC_56` | active hit streak, in games (`DELTA`) | walks back from the most recent game |
| `CALC_57` | actual minus expected hit rate (`DELTA`) | whole pull, or a passed `Window` |
| `CALC_58` | recent BABIP, plus `_SEASON` baseline and `_DELTA` | last 14 days vs. season |
| `CALC_59` | sweet-spot rate, launch angle 8 to 32 degrees | last 30 plate appearances |

**Spring training is excluded here, and only here.** A Statcast season pull includes spring
games: 7.8% of the probe batter's pitches and 10.1% of the probe pitcher's. At season
aggregate that is noise; across a 3-game or 7-day window in late March it is most of the
sample. It compounds, because Statcast appears not to compute expected statistics for spring games:
all 13 spring batted balls in the probe frame carried a null xBA, against 1 of 143 in the
regular season. That is one batter in one season, enough to justify the filter and not enough
to characterize Statcast's pipeline. Keeping them puts plate appearances into a denominator whose expected-stat numerator
silently vanishes. Filtering flipped the probe hitter's `CALC_57` from +0.0043 to -0.0183, a
sign change. Categories 1 through 4 do not filter; that is a real defect in shipped code and
is drafted for filing rather than repaired in passing.

**Every calculator takes `today`.** A calculator that reads the clock internally cannot be
evaluated against a past date, which is precisely what the validation harness in #35 has to
do. `today` threads to `apply_window`, whose `DAYS(n)` window ends *yesterday*, since today's
game has not been played.

**`CALC_57`'s two legs share one denominator, and that is the whole calculator.** An xBA
estimate exists only on batted balls, so a naive mean xBA runs near .41 while a hit rate runs
near .21; subtracting them yields about -.20 for every hitter alive, which looks like a
catastrophic slump and is a units error. Both legs here divide by the same plate appearances,
with a strikeout contributing 0 to both numerators and 1 to the shared denominator. Plate
appearances whose batted ball carries no xBA are dropped from both, since an unestimated hit
would manufacture the appearance of good luck.

**`CALC_52` is not the "hit in the last 3 games, Y/N" frequency** the ROADMAP's shorthand
suggests. That quantity is the model's *output* scale, the left-hand side of
`1 - (1 - p_hit)^PA_proj`, so feeding it back in as an input would apply the binomial twice.
The primary is hits per plate appearance; the multi-hit frequency rides along as a secondary.

Two smaller notes. `CALC_52` and `CALC_56` build a game log keyed on `game_pk` rather than
routing through `apply_window(GAMES(n))`, because that helper dedups by date string and would
collapse a doubleheader into one game. And role tags follow Category 4's stricter convention:
`PROBABILITY` is reserved for the three genuine per-plate-appearance hit rates, while
hard-hit rate, sweet-spot rate, xwOBA, and BABIP are `MULTIPLIER`. Category 1 tags its
hard-hit rate and xwOBA `PROBABILITY` "for uniformity" with a docstring warning instead; the
two conventions disagree, and Category 1's puts a .56 hard-hit rate and a .21 hit rate under
the same tag.

### Pitcher form and fatigue calculators

`category_09_pitcher_form.py` implements Category 9 (`CALC_60`-`CALC_65`), the pitcher-side
mirror of Category 8 and single-sided for the same reason. It inherits Category 8's
spring-training filter and `today` parameter, and adds one structural idea of its own:
**windows are counted in starts**, not days or games. Nothing here routes through
`apply_window`, which has no start-shaped window and could not easily gain one, because
identifying a start is a reconstruction rather than a filter.

| Calculator | Value | Window |
|---|---|---|
| `CALC_60` | mean Game Score v2 (`DELTA`, points) | last 2 starts |
| `CALC_61` | `_HITS_PER_9` and `_WHIP` | last 3 starts |
| `CALC_62` | fastball velocity minus season average (`DELTA`, mph) | last start vs. season |
| `CALC_63` | walk-rate delta, plus `_ZONE_DELTA` and `_MEATBALL` | last 3 starts vs. season |
| `CALC_64` | days since the last start (`DELTA`, days) | anchored to `today` |
| `CALC_65` | hard-hit rate allowed | last 2 starts |

**Two things Statcast does not publish, both reconstructed here.**

*A start.* There is no starter/reliever flag anywhere in the feed. The test used is that the
pitcher's earliest plate appearance in the game came in inning 1 with zero outs recorded,
which held for 19 of 19 games in the probe frame. It classifies an opener as a start, which
is the honest reading, and would misclassify a reliever who entered to begin the first
inning, which requires the starter to face nobody at all.

*Innings pitched.* The obvious approach, counting outs from plate-appearance `events`,
**undercounts**: a baserunner retired on a batted ball is an out that the batter's event does
not name, and that cost one out in 2 of 104 probe half-innings. The rule used instead leans
on game state. Within a start, every half-inning the pitcher appears in *except his last*
must have ended with him on the mound, so it contributed exactly `3 - outs_when_up` outs.
Only the final half-inning is ambiguous and falls back to the event map, so innings pitched
is exact except possibly there, where it is a lower bound. A lower bound on the denominator
makes `CALC_61` an upper bound, which is the safer direction for a statistic that flags a
struggling starter.

**Game Score v2, not Bill James's original.** v1 splits earned from unearned runs and
Statcast attaches no scoring decision to a run, so it is unreachable. Tom Tango's v2 is
`40 + 2*outs + K - 2*BB - 2*H - 3*R - 6*HR`, every term of which is available. Calibration is
left unclaimed: v2 is designed to sit on roughly v1's scale, but this repo has no league
sample to check that against, since the bulk `pybaseball.statcast()` pull is broken at the
pinned version (#38).

**Runs allowed carry a known, non-random undercount.** They are measured per half-inning as
the batting team's score at the end minus its score at the start, across all his pitch rows
rather than the terminal pitch of each plate appearance, because a run can score on a
non-terminal pitch (a wild pitch, a balk, a steal of home) and that cost one run in 1 of 19
probe starts. What no available signal fixes is a run charged to him that scores after he
leaves, driven home by a reliever. That biases `CALC_60` *upward* on exactly the starts where
he was pulled with runners aboard, which is to say on his worst ones.

Two smaller notes. `CALC_62` is signed and **negative is the interesting direction**: the
ROADMAP calls out a loss of 1.5 mph or more as a hit boost, so reading magnitude alone would
treat a velocity spike as a red flag. And `CALC_64`'s denominator is not a sample size, unlike
every other `Rate` in the model: rest is a single scalar read off one date, so there is
nothing to average and nothing to shrink. It is set to 1 to satisfy the shape and must not be
read as evidence weight.

### Ballpark and environment calculators

`category_05_ballpark_environment.py` implements Category 5 (`CALC_31`-`CALC_40`) and is the
**first game-level category**. A park factor, an air density, and a wind reading are
properties of the game, identical for all eighteen hitters in it, so those calculators take an
environment record rather than a player id. Only `CALC_33`, `CALC_37`, `CALC_38`, and
`CALC_40` are conditioned on the hitter.

| Calculator | Value | Source |
|---|---|---|
| `CALC_31` | park hit factor, 3-year rolling (`MULTIPLIER`) | `park_factors.json` |
| `CALC_32` | same, split by batter hand (`MULTIPLIER`) | `park_factors.json` |
| `CALC_33` | feet of fence distance vs. league mean, weighted by spray (`DELTA`) | Statcast + `ballparks.json` |
| `CALC_34` | degrees off the neutral band, plus `_TIER` (`DELTA`) | boxscore `Weather` |
| `CALC_35` | density-altitude index (`MULTIPLIER`) | `ballparks.json` + `Weather` |
| `CALC_36` | assisting wind in mph, projected onto spray (`DELTA`) | boxscore `Wind` + Statcast |
| `CALC_37` | `_BATTER` / `_PITCHER` hit rate in today's day-or-night condition | `statSplits` |
| `CALC_38` | `_BATTER` / `_PITCHER` hit rate home or away | `statSplits` |
| `CALC_39` | closed-roof factor vs. solved open-roof factor (`MULTIPLIER`) | `park_factors.json` |
| `CALC_40` | hit rate at today's venue | Statcast `home_team` |

**Two checked-in reference tables**, both generated on demand by `scripts/` and loaded through
`calculators/baselines.py`. `ballparks.json` holds elevation, roof type, and the five
published fence distances for all 30 venues; `CALC_33` needs a league mean per sector to say
whether today's park is deep, so evaluating one venue requires all of them. `park_factors.json`
holds Statcast's park-factor indexes, where 100 is neutral.

The park-factor table is a **scoped exception to the move off scraping**. The Stats API
publishes no park factors and pybaseball 2.0.0 exposes no park-factor function, so the
alternative was shipping `CALC_31`, `CALC_32`, and `CALC_39` as permanent `None`. The read
lives in `scripts/`, which never runs during a daily run or a test; its output is a checked-in
file, so no code path a run touches carries a scraper. Its window is 3-year rolling and
includes the in-progress season, so it is a snapshot of a moving quantity: regenerate
periodically and read `year_range` before trusting it.

**Spray angle is solved, and the sign was validated in both directions first.** Statcast
publishes `hc_x` / `hc_y` but no angle. A flipped sign turns every pull into an oppo and
leaves the output entirely plausible, which is the same failure class as `delta_run_exp`'s
perspective in Category 3. Negative is left field: a right-handed hitter distributed LF 62 /
CF 42 / RF 34 and an extreme left-handed pull hitter LF 34 / CF 62 / RF 132. Angles outside
fair territory are dropped rather than clamped, since a ball landing a few feet from the plate
solves to a wild angle. That drop pattern is also what validates the **origin**: a displaced
origin would misplace every ball, so deep drives would spill past 45 degrees too. They do not.
0 of 119 batted balls beyond 300 feet fell outside fair territory, against 13 of 152 under 150
feet. Error that vanishes as the lever arm grows is landing-point noise, not a bad origin.

Only fly balls and line drives feed the spray distribution. Ground balls never leave the
infield, and popups reach no fence while being the least reliable coordinate class there is.
Excluding popups costs 8 to 10 percent of tracked air balls and moves `CALC_33` by at most
0.73 feet and `CALC_36` by at most 0.15 mph, measured rather than assumed.

**Two traps around the venue string, both real.** `CALC_40` matches the home club's
abbreviation against Statcast's `home_team`, and the schedule must be requested with
`hydrate=team` to carry one at all: an unhydrated team object holds only `id`, `name`, and
`link`. Nor can the abbreviation come from `teams.py`'s crosswalk, which disagrees with
Statcast on 5 of 30 clubs (`ARI`/`KCR`/`SDP`/`SFG`/`TBR` against Statcast's `AZ`/`KC`/`SD`/
`SF`/`TB`). Either mistake makes `CALC_40` silently `None`, the second one at exactly five
parks and nowhere else.

**`CALC_39` solves for the open-roof index instead of dividing by the blend.** Savant
publishes no working roof-open grouping, but the closed share is exactly its `n_pa` over the
all-conditions `n_pa`, which makes `A = f*C + (1 - f)*O` invertible. Dividing by `A` directly
would attenuate the effect, because `A` already contains the closed games: American Family
Field reads .979 against the blend and .957 against the solved open index. Below a 15 percent
open share the inversion is abandoned, since half a point of rounding error on an integer
index becomes 3.3 points there. An open-air park and a fixed dome both return exactly 1.0, by
definition rather than as an invented neutral.

**`CALC_34` and `CALC_35` report physics, not league constants.** The ROADMAP asks for a
"temperature modifier" and an "index", but the map from air density to hit probability is a
league-wide measurement this repo cannot currently make (#38). So they report degrees off
neutral and a dry-air density ratio, and issue #39 owns the mapping, following `CALC_62`'s
precedent of reporting a velocity delta in raw mph. Humidity is unavailable from the boxscore
and is not guessed at; humid air is marginally less dense, so the index slightly understates
carry on muggy days.

**Weather and wind publish on a later clock than the lineup.** Of 8 Preview-state games
sampled on 2026-08-09, 2 carried weather and 6 did not, and it did not track start time. So
`CALC_34`, `CALC_35`, `CALC_36`, and `CALC_39` are fully usable for a backtest over completed
games and resolve to `None` more often than not at live pick time. When this is wired in it
needs the `refresh_opposing_pitchers` treatment: `queried_games_cache` freezes the first
boxscore read for the rest of the day, so a field that publishes later would never be seen.

Two overlaps to resolve before blending: `CALC_32` already contains `CALC_31`, and `CALC_35`
is computed from `CALC_34`'s temperature. And `CALC_34`, `CALC_35`, and `CALC_39` carry a
denominator of 1 that is **not** a sample size, the same shape as `CALC_64`'s rest days;
`CALC_33` and `CALC_36` carry the real tracked air-ball count.

### Lineup and game-context calculators

`category_06_lineup_game_context.py` implements Category 6 (`CALC_41`-`CALC_46`). This is
where quantities that change **how many plate appearances a hitter gets, and against whom**
live, which is why it owns the model's only two `EXPONENT` calculators.

`fetch_lineup` carries each player's `lineup_spot`, decoded from the boxscore's 3-digit
`battingOrder` encoding (`"100"` = spot 1, `"101"` = the first substitute batting there, so
`int(v) // 100` recovers the spot). `CALC_41` projects plate appearances from it across the
ROADMAP's 4.6-to-3.7 range, with role `EXPONENT` — it feeds `PA_proj`, never `p_hit`.

| Calculator | Value | Role |
|---|---|---|
| `CALC_41` | projected PAs from the batting-order spot | `EXPONENT` |
| `CALC_42` | preceding hitter's OBP | `MULTIPLIER` |
| `CALC_43` | on-deck hitter's OPS, plus `_SLG` | `MULTIPLIER` |
| `CALC_44` | `_PITCHER_TTO1/2/3`, `_BATTER_TTO1/2/3`, `_PENALTY` | `PROBABILITY` / `DELTA` |
| `CALC_45` | expected PAs lost to a skipped bottom 9th | `EXPONENT` |
| `CALC_46` | run total vs. league mean | `MULTIPLIER`, always `None` today |

`CALC_42` and `CALC_43` read a *different* hitter's line than the one being evaluated, so
`fetch_lineup_rates` resolves a whole batting order's season OBP/SLG/OPS in one hydrated
`/people` request rather than fetching each line twice as its neighbours come up. Both wrap at
the ends of the order: the hitter before the leadoff man is the ninth hitter.

**Times through the order is reconstructed, and the rule does not transfer between the two
sides.** Statcast publishes no TTO column and there is no situation code for it either.
Category 9's start test — earliest plate appearance in inning 1 with nobody out — works on a
pitcher's frame, which holds every batter he faced. It does **not** work on a batter's frame,
which holds only plate appearances involving that batter, so a pitcher's earliest row in it is
the first time he faced *this hitter*, usually the second inning or later. Applying it anyway
classified 35 of a probe hitter's 261 plate appearances as facing a starter, against a true
share near 60 percent. The batter side instead takes the starter to be whoever the hitter
faced in his own first plate appearance of the game, guarded to inning 3 so a pinch hitter
debuting in the ninth cannot crown a reliever. That guard was measured on bottom-of-order
regulars rather than on the top-of-order probes, where inning 1 is forced: their first plate
appearance landed in inning 2 in 19 of 52, 22 of 84, and 0 of 99 games, all admitted, while 6
and 1 genuine late entries were dropped.

Relief outings are dropped from the pitcher side for a related reason: every plate appearance
of a relief outing lands in bucket 1, since a reliever rarely faces the same hitter twice, and
keeping them drags bucket 1 toward bullpen quality. Buckets count repeat encounters with the
same batter rather than `ceil(index / 9)`; the two agree on 98.9 percent of the probe pitcher's
440 plate appearances and every disagreement is a substitution.

**The batter leg runs the opposite way from the pitcher leg, and that is a confound rather than
a finding.** A hitter only reaches bucket 3 when the starter lasted long enough, so batter-side
rates fall across buckets (.254/.148/.136) while the pitcher side rises (.251/.270/.337, a
+.085 penalty). Read the batter leg as "how this hitter does against a starter who has lasted".

`CALC_45` turns the ROADMAP's conditional "home hitters lose 0.5 PA if leading in the 9th" into
the unconditional expectation a projection needs, since at pick time nobody knows who will be
ahead. The skip rate is measured rather than assumed — 780 of 1,761 completed regular-season
games reaching nine innings, 44.29 percent — and lives in
`calculators/data/league_game_context.json`. Unlike every other league-reference constant in
this repo it is a genuine census, from the Stats API schedule rather than the broken bulk
Statcast pull, so it does not inherit #38. A road hitter returns exactly 0.0: the top of the
ninth is always played.

`CALC_46` returns `None` in every current code path. Neither a betting market's run total nor
the league mean of such totals is reachable, and defaulting that mean to a plausible-looking
8.5 would be the constant-from-nowhere `CALC_34` refused to invent. It ships as a signature so
the shape is recorded and an odds feed plugs straight in.

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
