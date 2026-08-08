# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
  and recorded in `AGENTS/authorized_libraries.md` (created in the same change; RULES §5 had
  been pointing at a list this project never created). Imported lazily inside the fetch
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
