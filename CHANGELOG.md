# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
