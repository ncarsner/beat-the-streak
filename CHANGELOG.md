# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

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
