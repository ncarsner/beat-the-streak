# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## 2026-08-12

### Added
- **`scripts/record_daily_picks.py` writes the rendered table to
  `data/picks/YYYY-MM-DD.txt`** beside the JSON, instead of leaving it in stdout. The JSON
  stays the durable record the grader reads; the text file is the half a person reads back
  later. `--show` re-derives it from an existing snapshot without re-evaluating the slate, so
  a day whose run only ever printed to a terminal can be given its table after the fact.
  Both writes sit behind the same shrink guard: a run that finds fewer games than the
  snapshot on disk overwrites neither file.

### Fixed
- **Games already underway were filtered by their scheduled start time rather than their
  actual state**, so eligibility rested on a plan instead of an observation. `fetch_schedule`
  now carries the schedule's `abstractGameState` and the new `main.has_started` reads it:
  "Preview" means not yet begun, anything else means begun. This drops a resumed suspended
  game whose start time still reads as future, and keeps a rain-delayed game whose hitters
  have not batted and remain perfectly pickable. Records without the field fall back to the
  clock, so nothing that predates it changes behavior.

---

## 2026-08-10

### Added
- **Forward test of the model against the heuristic**, the first work in the repo that
  produces evidence for #35. `scripts/record_daily_picks.py` snapshots a day's picks before
  first pitch; `scripts/grade_daily_picks.py` grades them once games are final and reports
  top-1 and top-5 hit rate, Brier and log loss for both methods.
  Snapshots live in `data/picks/`, **tracked in git unlike everything under `.cache/`**: a
  snapshot cannot be regenerated, because tomorrow the season pull already contains today's
  games.
- **Email notifications**, alongside SMS, on stdlib `smtplib` with no new dependency. Same
  contract as the SMS path: per-`GameHourUTC` grouping, top 5, a send returning False rather
  than raising, and a sent-cache marked only on success. The email carries every table
  column including `BB/K`, where the SMS carries name and probability only.
  Requires `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SUBSCRIBER_EMAIL`; optional
  `SMTP_PORT` (587) and `SMTP_FROM`.
- Table rendering for both forward-test scripts, plus `--show`, `--top-n` and `--detail`.

### Fixed
- **Every run that fetched a fresh lineup crashed.** `process_game_lineup` writes `start_dt`,
  a `datetime`, and `json.dump` cannot serialize it. The crash landed two statements before
  `probable_hitters`, so a run that fetched every hitter successfully printed no table, sent
  no SMS, and left a truncated cache file behind.
  **Cached runs survived degraded, which is why this went unnoticed**: entries written before
  `start_dt` existed carry no `game_pk`, so `player_model_probability` resolved the schedule
  to `{}` and the lineup side to `[]`, silently emptying Category 5, `CALC_41` and `CALC_70`.
  The broad `except` swallowed it. Verified after the fix: 102 of 108 keys resolve.
  The pre-existing round-trip test passed throughout because it round-trips a hand-built dict
  the producer has never written.
- **The grader scored an entire unplayed slate as no-hit.** A Pre-Game boxscore already
  publishes a fully zeroed batting line for every hitter in a posted lineup, indistinguishable
  from a real 0-for-4, so reading the boxscore alone reported a confident 0% base rate hours
  before first pitch. Game status now comes from the schedule.
- `--top-n 1` crashed the grader summary: both hit-rate columns became `top-1` and
  PrettyTable rejects duplicate field names.

### Changed
- `.github/workflows/sms-notify.yml` renamed to `notify.yml`, now driving both channels. The
  `schedule:` trigger stays commented out until #49 is fixed.

### Notes
- **The two methods disagree about who the best pick is, not merely how to score him.** On
  the 2026-08-10 slate of 49 hitters the heuristic spans 74.0 points and the model 24.3
  (sd 20.5 against 4.7), and the model's top pick sits 21st on the heuristic. Nothing here is
  validated: the ranking is still `Prob %` and #35 owns the question.
- A retrospective backtest is **structurally partial**, measured rather than assumed:
  replaying 2026-05-01 for one probe hitter surfaced 1573 of 2500 pitch records, 62.9%, dated
  after the replay date. Statcast is rewindable with an `end` bound; Category 1's
  `stats=vsPlayer` and the `statSplits` calls return season-to-date aggregates with no date
  parameter and cannot be rewound from the API at all.
- **#50's cost figure is overstated by roughly an order of magnitude.** Measured today: 50
  hitters cold in 5m27s, about 6.5s each, against the 78s per hitter that issue records as
  the reason the model is off by default under `--scheduled`.

---

## 2026-08-09 (Model column)

### Added
- **`Model` column in the ranked table**, beside `Prob %`. Pools 27 calculators, every
  shipped key reporting hits per plate appearance, weighted by sample size, through
  `CALC_76` with the Category 6 plate-appearance projection.
- **`Delta` column**, `Model` minus `Prob %` in percentage points, signed.
- `calculators/pipeline.py`, the single place that turns a hitter-game into all 108
  calculator keys. `main.py` and the coverage script both use it.
- `calculators.category_11_composite.aggregate_hit_rate` and `P_HIT_SCALE_KEYS`.
- `--model` / `--no-model`, defaulting to **on for a manual run and off with
  `--scheduled`**.
- `scripts/evaluate_sample_coverage.py`, measuring how many calculator values resolve for
  the `ROADMAP.md` sample batters against a day's posted lineups.

### Changed
- **Table sort reconciled.** `rank_key` is `(-probability, unresolved_flag, signed_delta)`:
  `Prob %` primary and descending, so highest probability is at the top and lowest at the
  bottom, with signed `Delta` as the secondary key. That places both required endpoints,
  highest probability with lowest delta first and lowest probability with highest positive
  delta last, which an absolute secondary key cannot do. Selection is unchanged and still
  runs off `Prob %`. A hitter the model could not evaluate has no delta rather than a zero
  one, and sorts last.
- **`Delta` currently breaks exact ties only.** On a slate where every hitter's `Prob %`
  differs it never fires, so the model has no influence on the ordering today. How much
  weight it should carry is #52.

### Notes
- **Coverage on the 2026-08-09 slate: 90.4% of 1,080 keys usable** across the 10 sample
  batters who were posted (93.8% excluding the four structurally blocked calculators).
  5.9% empty sample, 0% missing input.
- **The ranking is unchanged and still `Prob %`.** Neither number has been validated
  against outcomes (#35).
- **The two columns reorder, not just differ.** On the 2026-08-09 slate Freeman is fourth
  on the heuristic and second on the model, Harper first and fifth. The heuristic spans
  41.6 points and the model 10.6, which is what a 17-plate-appearance sample versus a
  several-thousand one should look like.
- **Membership is an explicit list, not a filter on the `PROBABILITY` role tag.** The tag
  does not mean what its name suggests: `CALC_04`, `CALC_05`, `CALC_07`, `CALC_08` and
  `CALC_06_XWOBA` all carry it and none is a hit rate. The subtle rejections are
  `CALC_16`/`17`/`18`/`30`, which look like batting averages but are per batted ball, so
  pooling them raises the aggregate by roughly the contact rate. `CALC_14` is excluded on
  correctness instead, per #37.
- Two known defects documented rather than fixed, because scoring the options needs #35:
  the aggregate over-weights the season baseline (#51), and it pools hits-per-PA with
  hits-allowed-per-PA, which is a modelling choice.
- The worst-covered sample hitter, at 85 of 108, was the one facing an opener: the opposing
  starter's Category 9 keys all return `None`, which is independent support for the
  `--exclude-openers` default.

### Fixed
- Three harness defects caught by the missing-input bucket while building the coverage
  script, each of which also affected the shared pipeline: `fetch_handedness` returns
  `bats`/`throws` rather than `bat_side`/`pitch_hand`; `fetch_game_environment` must be
  merged **under** the schedule half, not over it; and `arm_angle` is projected by
  Category 2, not Category 3.

---

## 2026-08-09 (Category 11 and the opener gate)

### Added
- `--exclude-openers` / `--no-exclude-openers` in `main.py`, **on by default**. Drops
  hitters whose opposing starter profiles as an opener rather than a conventional
  starting pitcher. Filtering is **per side**, so the other club's hitters are kept.
- Category 11 composite calculators (`CALC_72`-`CALC_76`) in
  `calculators/category_11_composite.py`. **The ROADMAP is now complete at 76 of 76.**

### Changed
- **Scope, per the user**: bullpens are disregarded and this tool ranks scheduled hitters
  against traditional starting pitchers. `CALC_74` takes no Category 7 input, and
  `CALC_47`/`CALC_48`/`CALC_49`/`CALC_50` are out of scope for the composite. Category 7
  is benched, not deleted; `CALC_51` was promoted out of it into the opener gate.
- `main.py` now imports from `calculators/` during a run, for the first time. The opener
  gate is the only such call; the ranked table is still computed from
  `binomial_probability`.

### Notes
- **The opener gate carries its own rule rather than calling `CALC_51`.** The calculator
  returns None for a pitcher who has never started, which is right as a measurement and
  wrong as a decision: a reliever announced as today's starter is the clearest opener
  there is. `is_opener` adds that case via `relief_share`.
- A hitter whose opposing starter is **unannounced is kept**. Dropping a playable matchup
  over a missing field is the worse failure.
- The gate runs after the lineup loop and never inside `queried_games_cache`, so it
  inherits `refresh_opposing_pitchers`: a game dropped for an announced opener returns on
  the next tick if he is scratched.
- **Category 11 did not need #34 or #39 resolved.** Sample-size weighting comes from the
  data, since every `Rate` carries its denominator. The one quantity that does not is how
  far to trust the prior, so `prior_strength` is a parameter defaulting to the season
  sample's own size. `CALC_75` uses the hitter's **own season rate** as the prior rather
  than a league mean, which does not exist while #38 is open.
- Two overlaps recorded rather than blended: `CALC_73` is `CALC_54`'s window with a decay
  and converges on it as the half-life grows, and `CALC_76` is the one `PROBABILITY` in
  the model that must never re-enter `p_hit`.
- Unwired: the ranked table is unchanged. Switching it to `CALC_76` is a deliberate call
  with no validation behind it, which is #35. 1468 tests passing.

---

## 2026-08-09 (Category 7)

### Added
- Category 7 opposing-bullpen calculators (`CALC_47`-`CALC_51`) in
  `calculators/category_07_bullpen_exposure.py`, with the source fetchers in
  `calculators/sources/category_07_bullpen_exposure.py`. All five work.
- `fetch_active_pitchers`, `fetch_pitching_game_logs` and `fetch_bullpen`, which together
  resolve an entire opposing bullpen with its season game logs in **two requests**.
- `starter_record`, which picks today's announced starter out of the roster pull rather
  than fetching him again. `attach_category_07` now returns both `bullpen` and `starter`,
  still in two requests, so `CALC_51`'s only real input path exists and is tested.

### Fixed
- `main.py` slate-date defect filed as #49: the date comes from the runner's clock, so the
  `0-2` UTC cron firings query the following day's schedule and never send. Not fixed in
  this change, which touches no runtime code.

### Notes
- **The category was not blocked, and the reasons it was thought to be were both wrong.**
  `GET /teams/{id}/roster?rosterType=active&date=` exists and honours `date`, verified at
  two in-season dates rather than one because a silently ignored `date` would return
  today's roster always and make every calculator here unbacktestable against a past slate.
  The starter/reliever split needs no Statcast either: every game-log line carries
  `gamesStarted`.
- **`CALC_47` and `CALC_50` aggregate over relief appearances rather than over pitchers**,
  which inverts the cost of a membership mistake. Admitting a starter is nearly free
  (a rotation arm has almost no relief lines to contribute) while dropping a real reliever
  costs his whole sample, so the threshold errs inclusive.
- Both boundary constants are measured over 129 active pitchers and 889 starts on ten
  clubs. The relief-share distribution is **not** the clean bimodal split a one-team sample
  suggests: 32 of 129 sit between the poles, so 0.5 is defended by where the straddling
  cases land (Mikolas .497 and Bello .495 below, Waldron .552 and Patrick .660 above).
  The opener boundary of 12 batters faced per start sits in the observed gap between 9.2
  and 16.0.
- **A rested bullpen reads 0.0, not `None`.** An empty recency window normally means "no
  evidence"; for `CALC_48` it means the reliever demonstrably did not pitch, which is the
  reading the calculator exists for.
- `CALC_50`'s whiff key is the only thing the Stats API does not publish at any endpoint
  checked, so it is an optional Statcast argument on the `CALC_05`-`CALC_08` precedent
  rather than a per-reliever pull nobody asked for.
- Spring training is filtered through `common.is_competitive`, which the game log supports
  for free. That is the filter #42 is open about Categories 1 through 4 missing.
- Unwired as always: `main.py` untouched, output table unchanged. Every live value
  independently reproduced from raw API JSON. 1396 tests passing.

---

## 2026-08-09 (Category 10)

### Added
- Category 10 defense and schedule-fatigue calculators (`CALC_66`-`CALC_71`) in
  `calculators/category_10_defense_schedule.py`, with the source fetchers in
  `calculators/sources/category_10_defense_schedule.py`.
- `resolved` on the Category 1 batter-versus-pitcher payload, distinguishing "the API
  answered" from "nothing came back".
- `latitude`, `longitude`, `timezone_id` and `utc_offset_hours` in
  `calculators/data/ballparks.json`. Additive: Category 5 reads none of them.

### Notes
- **Three of the six have no reachable source and ship as signatures**, matching
  `CALC_46`. `CALC_66`/`CALC_67` need Statcast Outs Above Average, which pybaseball 2.0.0
  does not expose (checked directly: its `fielding` helpers are Lahman and FanGraphs data).
  `CALC_68` needs a per-umpire zone index, which requires joining pitch data to officials
  across a league-wide pull that #38 blocks, plus a league mean of a quantity that cannot
  yet be computed. #46 tracks the decision. The umpire's **identity** is the reachable
  half and `fetch_home_plate_umpire` returns it today, recorded so a future session does
  not re-derive it.
- **`CALC_71` is the only calculator in the model whose value *is* the empty sample**, and
  that inverts the convention everywhere else. A null career line means "never faced",
  indicator 1.0 — but `empty_bvp()` produces the identical null line on a network failure,
  so the two were indistinguishable, and this is the one calculator for which they are
  opposite answers rather than both "no sample". Not hypothetical: the 2026-08-07 probe
  recorded the same batter-pitcher pair returning a populated response one minute and an
  empty split list the next, which is why `parse_bvp_stats` falls back to the API total at
  all. `parse_bvp_stats` now emits `resolved`, true only when the response carried a
  recognized stat group. Measured 2026-08-09: a pair that has faced returns
  `[vsPlayerTotal(1), vsPlayer(3)]` and a pair that has not returns `[vsPlayer(0),
  vsPlayerTotal(0)]` — both groups named, both empty — so the flag keys on group presence
  rather than on split contents. Three tests asserted the old ambiguity by exact equality
  and now assert the distinction.
- **The previous game is ordered by `(date, game number)`, not by date.** Category 8 keys
  its game log on `game_pk` for the same reason, but here a date-only order is worse than
  a collapse: on a doubleheader date "what was the previous game" has two different
  answers, and game two's is game one — zero travel miles and a turnaround measured in
  hours, which is precisely the fatigue signal these calculators exist to detect.
- **`CALC_70`'s time-zone shift is resolved from each venue's IANA zone at its own game
  date, never from the table's stored offset.** The stored value is a generation-time
  snapshot: Oracle Park reads `-7`, its July offset, and is `-8` in January. Arizona is
  what makes this matter rather than merely annoy — Chase Field does not observe daylight
  saving, so it sits at `-7` year round, meaning a July San Francisco to Phoenix trip
  crosses **no** time zones while an April one crosses one. `zoneinfo` is in the standard
  library, so getting this right costs nothing. The stored offset stays in the table as
  provenance, not as an input.
- **The haversine was validated against a real leg before anything depended on it.**
  Wrigley Field to Yankee Stadium solves to 715.1 miles against roughly 713 by independent
  reckoning; a swapped latitude and longitude, or a degrees-for-radians slip, yields a
  plausible-looking wrong number. The two 2026-08-02/03 legs reproduce the whole category:
  a Wrigley day game after a Wrigley night game gives the day-after-night indicator with
  0 miles, and the following Chicago-to-New-York game gives 715.1 miles, a +1 hour
  eastward shift and no off day.
- **`CALC_69`'s second key is named `_TURNAROUND_HOURS` and measured start to start.**
  Neither the schedule nor Statcast publishes the moment a game ended, so a 10:30pm finish
  before a 1:05pm start is 14.5 hours here and nearer 11 hours of real turnaround. The
  honest name prevents it being read as recovery time.
- **`CALC_70` reports three measurements rather than the ROADMAP's rule.** "Cross-country
  travel without an off day" is a rule over distance, time-zone shift and days of rest;
  the mileage threshold that would turn them into a penalty is a league constant #38
  blocks, so #39 owns the combination.
- Unwired as always: `main.py` byte-identical. 1288 tests passing, up from 1202.

---

## 2026-08-09 (Category 6 completion)

### Added
- `CALC_42`-`CALC_46` in `calculators/category_06_lineup_game_context.py`, completing the
  category around the `CALC_41` that shipped alone earlier, with the source fetchers in
  `calculators/sources/category_06_lineup_game_context.py`.
- `fetch_lineup_rates`, which resolves a whole batting order's season OBP/SLG/OPS in **one**
  hydrated `/people` request. That batching is what makes `CALC_42` and `CALC_43` cheap: each
  needs a *different* hitter's line than the one being evaluated, so per-player fetching would
  pull every line in the order twice.
- `calculators/data/league_game_context.json` and `scripts/generate_league_game_context.py`,
  the measured rate at which the home team never bats in the bottom of the ninth.

### Notes
- **Category 9's start test does not transfer to a batter frame, and reusing it silently
  destroys the sample.** That test asks whether a pitcher's earliest plate appearance in a game
  came in inning 1 with nobody out. A *pitcher's* frame holds every batter he faced, so it
  works there. A *batter's* frame holds only plate appearances involving that batter, so a
  pitcher's earliest row in it is the first time he faced *this hitter*, which for a starter is
  usually the second inning or later. Applying it anyway classified 35 of a probe hitter's 261
  plate appearances as facing a starter, against a true share near 60 percent. The batter side
  instead identifies the starter as whoever the hitter faced in **his own first plate appearance
  of the game**, guarded to inning 3 so a pinch hitter debuting in the ninth cannot crown a
  reliever. **The guard was measured where it is load-bearing**: two top-of-order probe hitters
  had their first plate appearance in inning 1 in 170 of 171 games, but that is forced, since a
  hitter batting 1 through 4 cannot come up later. Three bottom-of-order regulars are the real
  test, and there the first plate appearance landed in inning 2 in 19 of 52, 22 of 84 and 0 of
  99 games, all admitted by the guard, while it dropped 6 and 1 genuine late entries. Inning 3
  was never observed as a first plate appearance, so the bound carries an inning of headroom.
  The surviving share was 60 to 62 percent across all five hitters.
- **`lineup_by_spot` takes `main.fetch_lineup`'s actual return shape**, a list of per-player
  records in batting order, rather than a prebuilt `{spot: id}` mapping. Building that mapping
  is where the substitution collision lives: a boxscore encodes the spot in three digits, so
  `"100"` is the posted starter at spot 1 and `"101"` the first substitute to bat there, and
  `main.batting_order_spot` maps both to 1. The first occurrence wins, which keeps the posted
  starter, since a projection made before first pitch cannot know about a replacement that has
  not happened yet. Duplicates do not arise from a posted lineup (all six games sampled on
  2026-08-07 carried nine entries and nine distinct spots) but do once a game is under way,
  which is when a backtest over completed games would meet them.
- **Relief outings are dropped from the pitcher side**, for a related reason: every plate
  appearance of a relief outing lands in bucket 1, since a reliever rarely faces the same hitter
  twice. Keeping them would drag bucket 1 toward bullpen quality and inflate the apparent
  times-through-order penalty.
- **Buckets count repeat encounters with the same batter**, not `ceil(index / 9)`. The two agree
  on 98.9 percent of the probe pitcher's 440 plate appearances, and every one of the 5
  disagreements is a substitution, where counting encounters is right and counting lineup passes
  is wrong.
- **The batter leg carries a survivorship confound the pitcher leg does not, and it runs the
  opposite way.** A hitter only reaches bucket 3 when the starter was going well enough to still
  be in the game, so batter-side rates *fall* across buckets (.254/.148/.136 and .225/.214/.193
  on the two probe hitters) while the pitcher side rises (.251/.270/.337, a +.085 penalty). Read
  the batter leg as "how this hitter does against a starter who has lasted", not as his own
  fatigue curve.
- **`CALC_45`'s skip rate is measured, not assumed.** The ROADMAP states the loss conditionally,
  as 0.5 plate appearances when the home team leads, and a projection cannot condition on that
  because at pick time nobody knows who will be ahead. The unconditional expectation multiplies
  it by how often the home half is never played: 780 of 1,761 completed regular-season games
  reaching nine innings, 44.29 percent. **Unlike every other league-reference constant in this
  repo this one is a genuine league census**, from the Stats API schedule rather than the broken
  bulk `pybaseball.statcast()` pull, so it does not inherit #38. The ranged request's total was
  cross-checked against a day-by-day sum over 136 separate requests, identical on both counts,
  because a silently truncated large response has burned this project before (see
  `LEADERBOARD_LIMIT` in `generate_league_platoon_baseline.py`).
- **`CALC_45` is deliberately not spot-dependent**, unlike `CALC_41` in the same module. A
  skipped ninth removes roughly one lineup turn's worth of plate appearances, and which spots
  lose them depends on where the order stands after eight innings, which is close to uniform
  across games. A road hitter returns exactly 0.0, a measurement rather than a missing value:
  the top of the ninth is always played.
- **`CALC_42` models one of the two mechanisms the ROADMAP names.** "Pitcher forced into stretch"
  acts on this hitter's chance of a hit and is what the value reports; "more PAs" is a
  team-level effect on how deep the lineup bats, which belongs to the `PA_proj` lane this module
  already owns through `CALC_41` and `CALC_45`. Reporting the same on-base number under both
  lanes would invite the composite model to count it twice.
- **`CALC_43` emits both OPS and slugging**, since the ROADMAP says "threat" without defining it
  and both arrive in the same response. Slugging alone is arguably the better read of protection,
  since a pitcher fears extra-base damage rather than walks, so #39 rules on which is used. Same
  shape as `CALC_06` and `CALC_55`.
- **`CALC_46` returns None in every current code path, and that is the honest state.** Neither
  a betting market's run total nor the league mean of such totals is reachable: no source
  available here publishes betting lines, and there is no mean to be had of a quantity that
  cannot be fetched. It ships as a signature so the shape is recorded and an odds feed plugs
  straight in. Defaulting the league mean to a plausible-looking 8.5 would be exactly the
  constant-from-nowhere `CALC_34` refused to invent.
- One pre-existing test pinned `compute_category_06` to exactly `{"CALC_41": None}`. That
  contract widened legitimately, so it now asserts the `CALC_41` key's presence and value rather
  than the whole mapping, with the full key set covered in the new module.
- Unwired as always: `main.py` byte-identical, no Category 6 request made during a run.
  1199 tests passing, up from 1085. Every smoke-test value independently reproduced from raw
  pandas.

---

## 2026-08-09 (Category 5)

### Added
- Category 5 ballpark and environment calculators (`CALC_31`-`CALC_40`) in
  `calculators/category_05_ballpark_environment.py`, with the source fetchers in
  `calculators/sources/category_05_ballpark_environment.py`. The first **game-level**
  category: a park factor, an air density and a wind reading are properties of the game,
  identical for all eighteen hitters in it, so those calculators take an environment record
  rather than a player id. Only `CALC_33`, `CALC_37`, `CALC_38` and `CALC_40` are conditioned
  on the hitter.
- Two checked-in reference tables and their generators, following the
  `league_platoon_baseline.json` precedent, with loaders in `calculators/baselines.py`.
  `calculators/data/ballparks.json` (from `scripts/generate_ballparks.py`) holds elevation,
  roof type, surface and the five published fence distances for all 30 venues;
  `calculators/data/park_factors.json` (from `scripts/generate_park_factors.py`) holds
  Statcast's 3-year rolling park-factor indexes, all-batters and split by batter hand, plus
  the roof-closed grouping.
- Situational splits for `CALC_37` and `CALC_38` via `sitCodes=h,a,d,n`, one request per
  player covering both calculators. `fetch_stat_splits` is generalized to take its situation
  codes rather than hardcoding `vl,vr`, so Category 5 reuses it rather than copying it. That
  matters beyond tidiness: it already maps a pitching split's `battersFaced` onto
  `plateAppearances`, without which both calculators would return None for every pitcher.

### Notes
- **A deliberate, scoped exception to the 2026-07-11 move off scraping.** The MLB Stats API
  publishes no park factors and pybaseball 2.0.0 exposes no park-factor function (its `parks`
  and `park_codes` are Retrosheet identifiers), so the alternative was shipping `CALC_31`,
  `CALC_32` and `CALC_39` as permanent `None`. The exception is narrowed three ways: the read
  lives in `scripts/`, which never runs during a daily run or a test; its output is a
  checked-in JSON file, so no code path a run touches carries a scraper; and it parses an
  embedded JSON array rather than markup, raising on a page change instead of silently writing
  an empty table that every calculator would read as "every park is neutral".
- **The park-factor window moves.** It is 3-year rolling and includes the in-progress season
  (`year_range` "2024-2026"), so the file is a snapshot of a moving quantity rather than an
  annual constant. It carries `generated` and `year_range` so a stale table is detectable.
- **Savant's `venue_id` is the MLB venue id**, verified across the 29 venues the two tables
  share with zero name mismatches, and its roof-closed grouping covers exactly the 8 venues
  the Stats API reports as non-Open. Both cross-checks are asserted as tests, since they join
  two independently generated files. Savant carried 29 of 30 venues; Sutter Health Park has
  too little history for a three-year window, so `CALC_31` and `CALC_32` are None there.
- **Spray angle is solved from `hc_x`/`hc_y`, and the sign was validated in both directions
  before anything depended on it.** Statcast publishes no angle column, and a flipped sign
  turns every pull into an oppo while leaving the output entirely plausible, the same failure
  class as `delta_run_exp`'s perspective in Category 3. Negative is left field: a right-handed
  hitter distributed LF 62 / CF 42 / RF 34 and an extreme left-handed pull hitter LF 34 /
  CF 62 / RF 132. Angles outside fair territory are dropped rather than clamped (5 of 143 and
  15 of 243 batted balls, of which 15 of those 20 were popups or ground balls, where the
  landing point sits a few feet from the plate and the angle is numerically unstable). **That
  drop pattern is also what validates the origin**, which the sign check does not: a displaced
  origin misplaces every ball, so deep drives would spill past 45 degrees too. They do not.
  0 of 119 batted balls beyond 300 feet fell outside fair territory against 13 of 152 under
  150 feet, and error that vanishes as the lever arm grows is landing-point noise.
- **Only fly balls and line drives feed the spray distribution.** Ground balls never leave the
  infield; popups reach no fence, so they say nothing about the geometry `CALC_33` measures,
  and they are the least reliable coordinate class (10 of the 15 readings rejected as out of
  play for the left-handed probe hitter). The cost was measured rather than assumed, since
  dropping a batted-ball type also drops sample: 8 to 10 percent of tracked air balls, moving
  `CALC_33` by at most 0.73 feet and `CALC_36` by at most 0.15 mph.
- **Two traps around the venue string, both real and both silent.** `CALC_40` matches the home
  club's abbreviation against Statcast's `home_team`. The schedule must be requested with
  `hydrate=team` to carry one at all, since an unhydrated team object holds only `id`, `name`
  and `link`; `main.fetch_schedule` does not currently hydrate, which is a change that has to
  accompany wiring this in. And the abbreviation must **not** be resolved through `teams.py`'s
  crosswalk, the obvious-looking shortcut: it disagrees with Statcast on 5 of 30 clubs, holding
  ARI, KCR, SDP, SFG and TBR where Statcast publishes AZ, KC, SD, SF and TB. The Stats API's
  own `abbreviation` field matches Statcast on all 29 clubs observed, including ATH. Either
  mistake makes `CALC_40` permanently None, the second at exactly five parks.
- **`CALC_39` solves for the open-roof index rather than dividing by the all-conditions
  blend.** Savant publishes no working roof-open grouping, but the closed share is exactly
  closed `n_pa` over all `n_pa`, which makes `A = f*C + (1 - f)*O` invertible. The naive ratio
  is attenuated because `A` already contains the closed games: American Family Field reads
  .979 against the blend and .957 against the solved open index. Below a 15 percent open share
  the inversion is abandoned, since half a point of rounding error on an integer index becomes
  3.3 points there and 135 at Daikin Park's measured 0.4 percent. An open-air park and a fixed
  dome both return exactly 1.0, by definition rather than as an invented neutral.
- **The roof effect on base hits is small, and that is a finding.** Across the seven
  retractable parks the closed-roof hit index sat within two points of the all-conditions
  index, equal at the published integer resolution for three of them.
- **`CALC_34` and `CALC_35` report physics, not invented league constants.** The ROADMAP asks
  for a "temperature modifier" and an "index", but the map from air density to hit probability
  is a league-wide measurement #38 blocks. So they report degrees off the neutral band and a
  dry-air density ratio from the barometric formula and the ideal gas law, and #39 owns the
  mapping into `p_hit`, following `CALC_62`'s precedent of reporting a velocity delta in raw
  mph. Humidity is unavailable from the boxscore and is not guessed at; humid air is
  marginally less dense, so the index slightly understates carry on muggy days.
- **Weather and wind publish on a later clock than the lineup.** Of 8 Preview-state games
  sampled on 2026-08-09, 2 carried `Weather` and `Wind` and 6 did not, and it did not track
  start time (three games at the same 17:35Z first pitch split 1 populated, 2 not). So
  `CALC_34`, `CALC_35`, `CALC_36` and `CALC_39` are fully usable for the backtest in #35, over
  completed games where the fields are always present, and resolve to None more often than not
  at live pick time. When wired in they need the `refresh_opposing_pitchers` treatment:
  `queried_games_cache` freezes the first boxscore read for the rest of the day, so a field
  published later would never be seen.
- **Wind direction vocabulary enumerated in both directions**, from 78 games across five dates
  spanning April to August. A crossfield or calm wind returns 0.0 mph, which is a measurement,
  while an unrecognized direction returns None, so a vocabulary gap can never be mistaken for
  a calm day. The spread between two hitters is narrower than it looks like it should be, and
  that is physics: fair territory spans 90 degrees, so the cosine never falls below 0.31 and a
  wind out to right field still pushes a ball hit to left field outward.
- **Overlaps to resolve before blending.** `CALC_32` already contains `CALC_31`, and `CALC_35`
  is computed from `CALC_34`'s temperature; using either pair together double-counts the same
  adjustment. `CALC_34`, `CALC_35` and `CALC_39` carry a denominator of 1 that is **not** a
  sample size, the same shape as `CALC_64`'s rest days, while `CALC_33` and `CALC_36` carry the
  real tracked air-ball count.
- **`CALC_33` is distance-only.** The venue endpoint publishes no wall heights, so Fenway
  reads as a 310-foot left-field line with nothing to say the wall above it is 37 feet tall.
  The sign of the quantity's effect on hit probability is also deliberately not decided here:
  a shorter fence turns fly balls into home runs while a deeper outfield leaves more room for
  a ball to fall in, and which dominates is another #38-blocked measurement.
- Unwired as always: `main.py` byte-identical, no Category 5 request made during a run.
  1082 tests passing, up from 877. `CALC_33`, `CALC_35`, `CALC_36` and `CALC_40` were each
  independently reproduced from raw pandas and textbook formulas rather than only from the
  code under test.

---

## 2026-08-09 (Category 9)

### Added
- Category 9 pitcher trending-form calculators (`CALC_60`-`CALC_65`) in
  `calculators/category_09_pitcher_form.py`, with the source fetcher in
  `calculators/sources/category_09_pitcher_form.py`. The pitcher-side mirror of Category 8,
  single-sided, inheriting its spring-training filter and `today` parameter. Windows are
  counted in **starts**, so nothing here routes through `apply_window`.
- Start reconstruction. Statcast publishes no starter/reliever flag anywhere, so a start is
  identified as "the pitcher's earliest plate appearance came in inning 1 with zero outs",
  which held for 19 of 19 games in the probe frame. An opener classifies as a start, which is
  the honest reading. Relief appearances are dropped: every Category 9 calculator is about the
  starter a hitter is scheduled to face.
- Innings-pitched reconstruction, and **not by counting outs from plate-appearance events**.
  That undercounts, because a baserunner retired on a batted ball is an out the batter's event
  does not name: 2 of 104 probe half-innings came up one out short. The rule used instead
  leans on game state, since every half-inning the pitcher appears in except his last must
  have ended with him on the mound and therefore contributed exactly `3 - outs_when_up` outs.
  Only the final half-inning falls back to the event map, making innings pitched exact except
  there, where it is a lower bound.
- `CALC_60` uses Tom Tango's Game Score v2 rather than Bill James's original, which splits
  earned from unearned runs where Statcast attaches no scoring decision to a run. Calibration
  is left unclaimed: v2 is designed to sit on roughly v1's scale, but this repo has no league
  sample to check that against with the bulk pull broken at the pin (#38).
- Runs allowed measured per half-inning across all pitch rows rather than by summing terminal
  pitches, since a run can score on a non-terminal pitch (wild pitch, balk, steal of home) and
  that cost one run in 1 of the probe frame's 19 starts.

### Changed
- Fourth promotion to `calculators/common.py`: the `pitch_type` groupings
  (`FASTBALL_TYPES`/`BREAKING_TYPES`/`OFFSPEED_TYPES`, `pitch_class`) from Category 3, and the
  zone vocabulary (`IN_ZONE_CODES`/`OUT_OF_ZONE_CODES`, `zone_code`, `is_in_zone`,
  `is_out_of_zone`) from Category 4, now that Category 9 reads both. Moved verbatim, including
  `zone_code`'s integrality check and its numpy-duck-typing rationale, so the fix from earlier
  today is not lost to a retype. The measured tier constants stay in Category 3, since those
  describe one category rather than shared vocabulary. No behavior change.

### Known caveats
- **Runs allowed carry a non-random undercount.** A run charged to the starter that scores
  after he leaves, driven home by a reliever, never appears in his pitch rows and no available
  signal recovers it. That biases `CALC_60` upward on precisely the starts where he was pulled
  with runners aboard, which is to say on his worst ones.
- `CALC_64`'s denominator is **not a sample size**, unlike every other `Rate` in the model.
  Rest is a single scalar read off one date, so there is nothing to average and nothing to
  shrink; it is set to 1 to satisfy the shape and must not be read as evidence weight.
- The start test would misclassify a reliever who entered to begin the first inning, which
  requires the starter to face nobody at all.

### Verified
- 876 tests passing, ruff format and check clean, zero live network escapes from the suite.
- Smoke test against a real Statcast frame, with every value independently reproduced from raw
  pandas by a separate script: `CALC_60 = 40.0` (Game Scores 35 and 45), `CALC_61` 8.3455
  hits per nine and 1.2545 WHIP over 18.333 innings, `CALC_62 = -0.6206` mph,
  `CALC_63 = +0.0075` walk delta, `CALC_63_ZONE_DELTA = +0.0222`,
  `CALC_63_MEATBALL = 0.0614`, `CALC_64 = 4` days, `CALC_65 = 0.4865`.
- `main.py` byte-identical. Nothing in the package is called during a daily run.

---

## 2026-08-09 (later)

### Added
- Category 8 batter trending-form calculators (`CALC_52`-`CALC_59`) in
  `calculators/category_08_batter_form.py`, with the source fetcher in
  `calculators/sources/category_08_batter_form.py`. The first **single-sided** category and
  the first real consumer of `common.py`'s `Window` machinery: `DAYS(7)` and `DAYS(14)` for
  the calendar rates, `PLATE_APPEARANCES(30)` for the sweet-spot trend, and a game log for
  the game-anchored ones.
- Non-competitive game filtering. `common.NON_COMPETITIVE_GAME_TYPES` and
  `common.is_competitive` exclude spring training, exhibitions, and the All-Star game;
  postseason codes are deliberately not enumerated, so they are kept. A Statcast season pull
  includes spring games (7.8% of the probe batter's pitches, 10.1% of the probe pitcher's),
  and Statcast appears not to compute expected statistics for them: all 13 spring batted
  balls in the probe frame carried a null xBA, against 1 of 143 in the regular season. That
  is one batter in one season, enough to justify the filter and not enough to characterize
  Statcast's pipeline. Filtering flipped the probe hitter's `CALC_57` from +0.0043 to -0.0183, a sign change.
- A `today` parameter on every Category 8 calculator, threaded to `apply_window`. A
  calculator that reads the clock internally cannot be evaluated against a past date, which
  is what the validation harness in #35 has to do.
- `CALC_57` puts both of its legs over the same plate-appearance denominator. An xBA estimate
  exists only on batted balls, so a naive mean xBA (.41) minus a hit rate (.21) yields about
  -.20 for every hitter alive: a units error that reads as a catastrophic slump. Plate
  appearances whose batted ball carries no xBA estimate are dropped from both legs, since an
  unestimated hit would manufacture the appearance of good luck.
- `CALC_52` is a per-plate-appearance hit rate, not the "hit in last 3 games, Y/N" frequency
  the ROADMAP's shorthand suggests. That frequency is the model's output scale, so feeding it
  back in as a `p_hit` input would apply the binomial twice; it rides along as
  `CALC_52_MULTI_HIT` instead.

### Changed
- `SWING_DESCRIPTIONS`, `WHIFF_DESCRIPTIONS`, `CONTACT_DESCRIPTIONS`, `IN_PLAY`, and
  `HARD_HIT_MPH` promoted to `calculators/common.py`. Categories 1, 4, and 8 had each defined
  the same vocabulary independently; they agreed on membership at the time of the move, which
  is the good case and not one to rely on twice. Same reasoning as the `HIT_EVENTS` and
  `terminal_pitch_by_pa` promotions. No behavior change.

### Known caveats
- **Categories 1 through 4 do not filter spring training** and are computed over samples that
  are 8 to 10 percent spring games. For Categories 3 and 4 it is worse than dilution: their
  expected-stat keys (`CALC_16`/`17`/`18`, `CALC_30`) silently drop spring batted balls from
  their numerators while the rate keys in the same categories count spring pitches in their
  denominators. Drafted for filing as an issue rather than repaired here.
- Role tags in Category 8 follow Category 4's stricter convention, reserving `PROBABILITY`
  for p_hit-scale rates. Category 1 tags its hard-hit rate and xwOBA `PROBABILITY` "for
  uniformity" with a docstring warning. The two conventions disagree; Category 1's puts a .56
  hard-hit rate and a .21 hit rate under the same tag.
- `CALC_58`'s baseline is a **season** baseline, not the career one the ROADMAP also mentions.
  The source is season-scoped, so a career BABIP needs a multi-season pull that does not
  exist yet.

### Verified
- 793 tests passing, ruff format and check clean, zero live network escapes from the suite.
- Smoke test against a real Statcast frame, run twice: once at the true current date and once
  anchored to the batter's last game, which is what exercises the recency windows at all.
  Every populated value was independently reproduced from raw pandas, including
  `CALC_57 = -0.0183` and the `CALC_52` sample of 14 plate appearances across three games.
- `main.py` byte-identical. Nothing in the package is called during a daily run.

---

## 2026-08-09

### Added
- Category 4 plate-discipline and zone-location calculators (`CALC_24`-`CALC_30`) in
  `calculators/category_04_plate_discipline.py`, with source fetchers in
  `calculators/sources/category_04_plate_discipline.py`. Statcast pitch-level at the existing
  pybaseball pin, no new dependency and no new endpoint. Every calculator is two-sided and
  emits two keys, the hitter's rate and the pitcher rate that pairs with it, following the
  `CALC_16`/`CALC_16_USAGE` precedent rather than multiplying two unrelated rates together.
- `OUT_EVENTS` and `ON_BASE_EVENTS` in `calculators/common.py`, enumerated explicitly in both
  directions rather than one set plus a complement rule. Statcast ends plate appearances on
  values that do not resolve the batter at all (`truncated_pa`, `caught_stealing_2b`,
  `pickoff_1b`), and a complement would score every one of them as a pitcher out, which is
  the same silent-negative shape as the `CALC_14` defect in issue #37. An event in neither
  set is dropped from numerator and denominator. Verified against the probe frames: 774 of
  774 plate appearances classified, zero residue.
- Swing, whiff, contact, and unthrown-pitch description vocabularies. A `foul_tip` counts as
  contact, not a whiff, matching Savant and the rule that makes a caught foul tip a strikeout.
  Pitch-timer violations (`automatic_ball`) are excluded from `CALC_28` by name, since the
  batter had nothing to swing at and the pitcher threw nothing to locate; 4 of the probe
  frames' 17 landed on `pitch_number == 1`, squarely inside that calculator's population.
- `zone_code`, which coerces Statcast's `zone` to an int before testing it against the code
  sets. pandas widens the column to float64 to hold nulls and the cache's CSV round-trip
  preserves that, so an uncoerced `5.0 in IN_ZONE_CODES` would match nothing and silently
  empty every zone-based rate in the category. The coercion is integrality-checked rather
  than a plain `int()`: `zone` is categorical, so a 9.7 is not a code that rounds to a
  neighbor, and truncating it would place a malformed reading inside the strike zone.
- `CALC_30_COVERAGE`, weighted by the starter's own location distribution rather than counted
  as covered-zones-over-nine. `CALC_30` renormalizes its weighted xBA over the zones where the
  hitter has a batted-ball sample, which is what keeps it on a batting-average scale but also
  makes a partial heatmap look like a full one; coverage is the honest statement of how much
  of the starter's real distribution the average represents. Its denominator is the starter's
  in-zone pitch count, the sample size behind its own rate, so #34's shrinkage reads it the
  same way it reads every other `Rate`.

### Changed
- `_terminal_pitch_by_pa` moved from `calculators/category_03_pitch_arsenal.py` to
  `calculators/common.py` as the public `terminal_pitch_by_pa`, now that a second category
  needs it. Two copies would eventually disagree, which is the same reason `HIT_EVENTS` was
  promoted on 2026-08-08. Category 3's behavior is unchanged.

### Verified
- 717 tests passing, ruff format and check clean, zero live network escapes from the suite.
- Smoke test against real Statcast frames for one hitter and one starter (a live pull, since
  the current-season cache is date-keyed and the previous day's files had aged out): all 14
  keys populated, every value independently reproduced from raw pandas over the same frames
  by a separate script, and the zone and out-of-zone rates summing to exactly 1.0.
- `main.py` byte-identical, `grep -c calculators main.py` returns 0. Nothing in the package is
  called during a daily run.

### Known caveats
- `CALC_29`'s two-strike contact rate runs above the same hitter's overall contact rate (.764
  against .694 on the smoke-test hitter). This is structural, not a defect: every foul with
  two strikes keeps the plate appearance alive at two strikes, so the denominator is
  self-weighted toward hitters who battle. Documented at the calculator.
- Per-zone xBA samples are thin, 5 to 25 batted balls per zone over a regular hitter's full
  season, which makes shrinkage (#34) matter more for `CALC_30` than almost anywhere else in
  the model.

---

## 2026-08-08

### Added
- Category 3 pitch-arsenal calculators (`CALC_16`-`CALC_23`) in
  `calculators/category_03_pitch_arsenal.py`, with source fetchers in
  `calculators/sources/category_03_pitch_arsenal.py`. Entirely Statcast pitch-level, at the
  existing pybaseball pin — no new dependency and no new endpoint. Every calculator takes both
  sides of the matchup; the hitter's side is not restricted to today's starter, which is what
  gives the category a sample where Category 1 has none.
- `CALC_16`/`17`/`18` emit two keys each (class xBA and the starter's usage of that class)
  rather than their product, which is neither an expected average nor a usage. Usage divides
  by typed pitches, not by the three classes' sum, so the unclassified residue stays visible.
- `_terminal_pitch_by_pa`: Category 3 filters on attributes that vary within a plate
  appearance, so each PA is reduced to the pitch that ended it before any tier filter runs.
  Distinct from Category 2's `_plate_appearances`, which keeps every pitch of a PA because it
  filters on attributes constant across one.
- `vertical_approach_angle`, solved from the release-point velocity and acceleration vectors —
  Statcast publishes the trajectory terms but not the angle. Sign validated by the ordering it
  produces: four-seamers flattest at -4.63 degrees, curveballs steepest at -9.52.
- Tier boundaries for velocity, approach angle, horizontal break, and release extension, all
  measured against 20,545 pitches seen by 12 regular hitters over the 2026 season rather than
  assumed. `EXTREME_PFX_X_FEET` carries its unit in the name: `pfx_x` is in feet and Savant
  displays inches.
- Category 2 platoon/handedness calculators (`CALC_09`-`CALC_15`) in
  `calculators/category_02_platoon_splits.py`, plus `CALC_41` (lineup-spot PA expectation) in
  `calculators/category_06_lineup_game_context.py`.
- Two shared types in `calculators/common.py` that every later category inherits: `Window`
  (`CAREER`/`SEASON`/`DAYS(n)`/`GAMES(n)`/`PLATE_APPEARANCES(n)`/`SEASONS(n)`, applied as a
  local slice over one season-wide fetch) and roles (`PROBABILITY`/`MULTIPLIER`/`EXPONENT`/
  `DELTA`), so a calculator declares how the composite may consume it rather than the composite
  keeping a lookup table of what 76 numbers mean.
- Statcast response cache at `.cache/statcast/{role}_{player_id}_{season}[_{date}].csv.gz`.
  Past seasons are immutable and never expire; the current season is date-keyed. `.csv.gz`
  specifically to avoid a `pyarrow` dependency. Measured saving: 3.4s per batter pull, 4.6s
  per pitcher pull, on a library version with no cache of its own.
- Source fetchers for Category 2: batched handedness (`GET /people?personIds=`, one request
  for an entire slate), `statSplits` for both stat groups, and Statcast pitcher/batter pulls.
- League 2x2 platoon baseline (`scripts/generate_league_platoon_baseline.py` +
  `calculators/data/league_platoon_baseline.json`), generated on demand from 4 Stats API
  requests. Loader in `calculators/baselines.py`.
- `lineup_spot` on every lineup entry, decoded from the boxscore's 3-digit `battingOrder`
  encoding (`"100"` = spot 1, `"101"` = first substitute batting there).
- Autouse network guard in `tests/conftest.py`: any test opening a non-loopback socket now
  fails. The suite documented itself as offline long before anything enforced it, and it had
  silently broken twice.

### Changed
- `calculators/sources.py` split into a `sources/` package and `test_suite.py` into a `tests/`
  package, both mirroring the calculator module layout.
- `CALC_01`-`CALC_08` migrated onto the shared `Window` and role types; `RECENT_WINDOW_YEARS`
  is now `SEASONS(3)`. One calculator contract across the package, not two.
- Documented `uv` as the project's package manager. Nothing on disk recorded it, and
  `requirements.txt` implied pip.
- `HIT_EVENTS` moved from `category_02_platoon_splits.py` to `calculators/common.py`. Every
  category that counts hits off pitch-level rows needs the same set, and two copies would
  eventually disagree. Nothing imported it from Category 2, so the move is internal.

### Fixed
- A test in the Category 1 source suite patched `fetch_bvp_stats` but not `fetch_bvp_statcast`,
  so every run scraped Baseball Savant for real. It never failed; the only symptom was a 0.43s
  test in a suite where nothing else exceeded 0.05s.

### Notes
- **Nothing in `calculators/` is called during a run.** No BvP, handedness, or Statcast request
  is made, `binomial_probability` still computes `pa / 5`, and the ranked table is unchanged.
- Rates in Category 2 are per plate appearance, not per at-bat. The ROADMAP words several as
  "BA", but `p_hit` is defined per PA and combined as `1 - (1 - p_hit)^PA_proj`; an H/AB rate
  in a per-PA exponent overstates by roughly ten percent.
- `sitCodes` and date ranges do not compose on the MLB Stats API, which is why the two 14-day
  calculators are Statcast-derived: `byDateRange` honors the window and drops the split,
  `statSplits` honors the split and ignores the window.
- The pitching stat group has no `plateAppearances` key. `battersFaced` is mapped onto it at
  the source boundary; without that, `CALC_11` returns `None` for every pitcher, silently.
- `pybaseball.statcast()` is broken at the pinned 2.0.0 (`KeyError` on `pitcher.1` /
  `fielder_2.1` in `postprocessing`). `statcast_batter` and `statcast_pitcher` are unaffected.
  The exact pin protects a working consumer from source drift; it does not protect one that
  was already stale.

---

## 2026-08-07

### Added
- `calculators/` package, one module per `ROADMAP.md` category, with a naming convention meant
  to carry all 11: `category_NN_<slug>.py` modules holding `calc_NN_<slug>` functions and a
  `compute_category_NN` aggregate, `common.py` for shared types and counting-stat helpers, and
  `sources.py` as the single module that touches the network. Pure calculator modules never
  import `sources`, so their arithmetic tests need no request mocking. The package's
  `__init__` re-exports only the cross-category surface (`Rate` and the counting-stat helpers);
  calculators are imported from the module that owns them, so eleven categories cannot collide
  in one flat namespace.
- `calculators/category_01_bvp_matchups.py`: Category 1 (batter-vs-pitcher) calculators —
  `CALC_01` career BvP hit rate, `CALC_02` season BvP hit rate, `CALC_03` trailing 3-calendar-year
  BvP hit rate, and `CALC_04` BvP contact rate. Pure functions with no network I/O; each returns
  a `Rate` carrying both the value and the sample size behind it, or `None` when the pair has no
  shared history.
- `parse_bvp_stats`: normalizes a raw MLB Stats API `stats=vsPlayer` response into
  `{"career": ..., "by_season": {...}}`. Career is summed from the per-season splits, with the
  API's `vsPlayerTotal` group used only as a fallback. Both groups proved independently
  unreliable on the same batter-pitcher pair minutes apart: a `vsPlayerTotal` of 2 PA against a
  season split of 3 PA, then an empty season-split list alongside a populated 3 PA total.
  Deriving career from the splits keeps `CALC_01`'s sample from ever being smaller than
  `CALC_03`'s window over the same matchup.
- `fetch_bvp_stats` / `attach_category_01` in `calculators/sources.py`: the I/O half of
  Category 1, where
  one `vsPlayer` request per batter covers all four calculators. A batter with no announced
  opposing starter gets all-`None` calculator keys without a request being made. **Neither is
  called during a run yet** — see Notes.
- `CALC_05`-`CALC_08`, the Statcast half of Category 1: `calc_05_bvp_hard_hit_rate`,
  `calc_06_bvp_xba` / `calc_06_bvp_xwoba`, `calc_07_bvp_whiff_rate`, and
  `calc_08_bvp_putaway_rate`. Pure functions over a list of normalized pitch records; swing and
  whiff membership is pinned by an explicit `description`-by-`description` test, and a tipped
  ball counts as a swing but not a miss per ROADMAP's "swings and misses" wording. `CALC_06`
  carries two metrics and is emitted under `CALC_06_XBA` and `CALC_06_XWOBA`.
- `fetch_bvp_statcast` in `calculators/sources.py`: pulls a season of Statcast pitches for a
  batter via pybaseball, filters to the opposing starter, and normalizes to plain dicts with
  `None` in place of NaN, so no DataFrame crosses into the calculators. Season-scoped, since
  Statcast starts in 2015 and a call pulls one season — a different span than `CALC_01`'s
  career window. Returns `[]` on failure rather than raising.
- **pybaseball 2.0.0** added to `requirements.txt`, authorized by the repo owner on 2026-08-07
  and recorded in `AGENTS/authorized_libraries.md` — created in the same change, but note
  `AGENTS/` is gitignored, so that record is local-only and this entry is the tracked one.
  RULES §5 had been pointing at a list this project never actually created. Imported lazily inside the fetch
  function, never at module scope, so neither the daily run nor most of the test suite pays for
  pandas. `.github/workflows/sms-notify.yml` installs `requests` only and does not read
  `requirements.txt`, so the cron is unaffected.
- `mlb_api.py`: single home for `MLB_API_BASE`, now that both the run path and the calculator
  fetch layer hit the same host. Previously defined in `main.py` only.
- `probable_pitcher_id` and `hydrate=probablePitcher` on the `/schedule` request: `fetch_schedule`
  records now carry `home_pitcher_id` / `away_pitcher_id` (`None` until announced).

### Changed
- `process_game_lineup` now attaches `opposing_pitcher_id` to each lineup entry, resolving each
  batter against the *other* side's probable starter before the home/away lists are flattened.
  Lineup entries cached earlier the same day predate this field and degrade to `None` rather than
  failing.
- `refresh_opposing_pitchers`: cached lineup entries have their opposing starter re-resolved (by
  `team_id`, since the cache does not record which side a batter was on) on every run, and the
  cache entry is updated in place. A lineup can post before the probable pitcher is announced —
  without this, the `None` written on that tick would stick for the rest of the day, since the
  same-day cache short-circuits the lineup fetch. No extra request is made.
- `README.md`: corrected the documented `MAX_PLAYERS` default from `10` to `50`, which it has
  been since commit `f812694`.

### Notes
- Category 1 is deliberately not wired into the daily run: no calculator is invoked, no BvP
  request is made, and `probable_hitters` is unchanged line for line. These are being solidified and tested
  ahead of the composite model, where they will be consumed together. Shrinking a small BvP
  sample toward a prior belongs in `CALC_75` (Bayesian composite), not in the current
  last-5-game binomial ranking.
- `fetch_schedule`'s `probablePitcher` hydration and the per-batter `opposing_pitcher_id` *are*
  live, since both are free — the hydration rides an existing request and the resolution is
  local. They are the inputs `fetch_bvp_stats` will need.

---

## 2026-07-26

### Changed
- `.github/workflows/sms-notify.yml`: disabled the 15-minute `schedule:` cron trigger
  (commented out, not removed) and replaced it with `workflow_dispatch:` for manual-only
  runs, since every scheduled tick was failing while Twilio Toll-Free Verification for
  `TWILIO_FROM_NUMBER` remains pending. Re-enable the cron once the number is approved.

---

## 2026-07-24

### Added
- SMS notifications via Twilio: `--scheduled` runs now send a per-grouping text message listing
  the top 5 players by hit probability for each `GameHourUTC` window. Manual (flag-less) mode
  never triggers a Twilio call.
- `group_picks_by_start_time`: groups a run's qualifying players by `GameHourUTC` and ranks the
  top 5 (or fewer) per group by hit probability.
- `format_sms_body` / `send_sms_notification`: format and POST a per-grouping message to
  Twilio's Messages API using HTTP Basic Auth; a non-2xx response or `requests.RequestException`
  is caught and does not raise out of the function.
- `dispatch_scheduled_sms`: orchestrates grouping, per-grouping cache checks, SMS dispatch, and
  cache writes during `--scheduled` runs.
- Per-grouping SMS-sent cache (`.cache/sms_sent_cache.json`, date-scoped by `GameHourUTC`): a
  grouping is marked sent only after a confirmed 2xx response; a failed send leaves it unmarked
  so the next `--scheduled` run retries automatically.
- Missing-credential guard in `dispatch_scheduled_sms`: if any of `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, or `SUBSCRIBER_PHONE_NUMBER` is unset, the SMS
  step is logged and skipped without crashing the run or blocking table output.
- GitHub Actions workflow (`.github/workflows/sms-notify.yml`): 15-minute cron
  (`*/15 14-23,0-2 * * *`, covering 10am–10pm EDT), gated by `vars.SEASON_ACTIVE`, with the
  four Twilio secrets wired via `env:` and `actions/cache` persisting `.cache/` across
  same-day runs.
- `CONSENT.md`: SMS opt-in consent statement for the single-subscriber notification feature,
  hosted as the proof-of-consent link for Twilio Toll-Free Verification.

### Fixed
- `queried_games_cache.json` previously stored only a bare `{gamePk: date}` flag, so once a
  game's lineup was fetched successfully, every later run that same day skipped the game
  entirely and lost its players from the stdout table, not just the redundant lineup API
  call. The cache now stores the fetched player list alongside the date
  (`{gamePk: {date, players}}`), so a same-day cache hit still contributes those players to
  the run's output while still skipping the network call.

---

## 2026-07-20

### Added
- `fetch_lineup(game_pk)`: calls `/game/{gamePk}/boxscore` and returns the posted batting-order
  lineup as `{home: [...], away: [...]}` lists of `{id, fullName, team_id}` dicts. Empty or
  absent `battingOrder` and request failures both return empty lists without raising.
- `select_games(games, now, scheduled)`: filters the day's schedule to the relevant window.
  Manual mode (default) includes all games starting at or after `now`; scheduled/cron mode
  (`--scheduled`) includes only games starting within the next 2 hours.
- `--scheduled` CLI flag (store_true): selects cron/scheduled window logic. Omitting it
  selects manual mode. Intended for recurring runs every 15–20 minutes.
- Per-gamePk queried cache (`.cache/queried_games_cache.json`, date-scoped): records which
  games have already had their lineup fetched and players queried. Subsequent same-day runs
  skip any already-queried `gamePk`, preventing duplicate stat queries on repeated cron fires.
- `TEAM_ID_TO_ABBR` reverse index in `teams.py`: numeric team ID → abbreviation, derived from
  `TEAM_CROSSWALK`. Team resolution no longer requires a name-match step.

### Changed
- Player pool is now fully dynamic: determined at runtime from posted MLB lineups rather than
  a static `players.py` / `selected_hitters` list.
- `scrape_player_data` now accepts `(player_id, player_name, team_id)` directly from lineup
  data, with no `/people/search` name-search step.
- No-data cache (`.cache/no_data_cache.json`) rekeyed by numeric player ID instead of player
  name.
- `fetch_schedule` now returns a list of per-game records (gamePk, gameNumber, home/away
  team_id, start datetime); doubleheader games appear as distinct entries.
- `MAX_PLAYERS` is now a post-selection safety cap on the total player list, not a
  pool-slicing mode control.

### Removed
- `--mode {subset,max,full}` CLI flag and the `resolve_run_config` branching it drove.
- `players.py` and the static `hitters` / `selected_hitters` pool.
- `lookup_player_info`, `_player_id_cache`, and all `/people/search` name-lookup logic.
- `.cache/missing_team_cache.json` and its load/save functions — name-match gaps no longer
  occur now that team resolution is ID-based.

---

## 2026-07-14

### Added
- `fetch_schedule(date)`: one request to `/schedule?sportId=1&date=<date>`, returning
  `{team_id: game_hour_utc}` for every team playing that day. Doubleheaders are represented
  once via `gameNumber == 1`; `Postponed` games are excluded.
- `GameHourUTC` field on each player's compiled data (`scrape_player_data`,
  `compile_player_data`), resolved from the crosswalk-derived `team_id` against the day's
  schedule map. `None` when the team has no game that day or the crosswalk lookup missed.
  Data-only this phase — no new table column, no timezone conversion.
- `.cache/schedule_fetch_errors.log`: plain-text, append-only log of `/schedule` request
  failures (`YYYY-MM-DD HH:MM:SS — <exception>`), console-and-file (not file-only, unlike the
  crosswalk-gap cache) since a schedule fetch is a rare whole-run failure rather than a
  frequent per-entity one.

### Changed
- `__main__` now fetches the day's schedule once per run and threads it through
  `compile_player_data` -> `scrape_player_data`, at no extra per-player request cost.

## 2026-07-13

### Added
- `teams.py`: static `TEAM_CROSSWALK` mapping all 30 MLB team names to `{id, abbreviation}`,
  seeded from the MLB Stats API `/teams` endpoint.
- `Team` column in the ranked output table, showing each player's current team abbreviation.
- Persisted `.cache/missing_team_cache.json` recording any player's current team name that
  isn't yet in `TEAM_CROSSWALK`, so gaps can be reviewed and fixed without re-running the
  tool; entries auto-prune once the crosswalk is updated to cover them.

### Changed
- `lookup_player_id` renamed to `lookup_player_info`; now requests `hydrate=currentTeam`
  on the existing `/people/search` call and returns/caches `{id, team_name}` per player,
  at no extra request cost.
- 5 team abbreviations in `TEAM_CROSSWALK` corrected to true 3-letter codes (`AZ`->`ARI`,
  `KC`->`KCR`, `SD`->`SDP`, `SF`->`SFG`, `TB`->`TBR`), consistent with every other entry.

### Fixed
- The per-player fetch-progress line no longer prints for players skipped on cooldown; it
  previously printed `"Fetching {player} ..."` before the cooldown check ran, implying an
  API call was attempted when it never was.

## 2026-07-11

### Fixed
- Crash when the MLB Stats API returned a present-but-empty `stats` list for a player with no game log for the season, which raised an uncaught `IndexError` and stopped the whole run before any output printed (PR #2).

### Changed
- Rewrote `test_suite.py` with parametrized pytest cases covering the current MLB Stats API implementation, including the empty-stats regression (PR #2).
- Curated `selected_hitters` list adjusted (swapped in a player, trimmed several names).

### Changed (PR #1)
- Replaced the Baseball-Reference HTML scraper with the official MLB Stats API, removing reliance on scraping and page-structure parsing.
- Added a per-player progress indicator during fetches.
- Added a `MAX_PLAYERS` cap to limit run size for validation before scaling up.
- Rewrote the README to document setup and usage against the current implementation.

### Fixed (PR #1)
- Data-retrieval bugs in the prior scraper implementation that caused the tool to silently return no data.

### Removed (PR #1)
- `config.ini`-based `User-Agent` configuration, no longer needed since the MLB Stats API requires no custom headers.

### Added (PR #4)
- `--mode {subset,max,full}` CLI flag selecting the player pool and cap per run: `subset`
  (curated list, capped), `max` (full pool, capped), or `full` (full pool, uncapped).
- Persisted no-data cache (`.cache/no_data_cache.json`) that skips players who recently
  polled with no recent data, instead of re-fetching them every run.
- `--cooldown-days` CLI flag (default 7) controlling how long a no-data result is
  remembered before a player is rechecked.

### Changed (PR #3)
- Rewrote `README.md` to document the current MLB Stats API implementation; it previously
  still described the retired Baseball-Reference scraper.

### Added (PR #3)
- This `CHANGELOG.md`, summarizing project history from the commit log.

## 2024-08-23

### Changed
- Minor updates.

## 2024-08-10

### Changed
- Minor updates.

## 2024-08-08

### Added
- More players added to the pool.

### Removed
- `config.ini` deleted from version control (moved to `.gitignore`).

### Changed
- Minor updates; branches merged.

## 2024-08-06

### Changed
- Minor updates.

## 2024-08-04

### Added
- More players added to the pool.

### Changed
- Script cleanup and minor updates.

## 2024-08-02 – 2024-08-03

### Added
- Initial version of the tool.
