# CLAUDE.md — Project State

Persistent project memory for Claude sessions on this repo. Agent behavior rules live in
`AGENTS/CLAUDE.md` (gitignored, local-only); this file tracks the repo itself.

## Current Phase

Working tool. Fetches recent batting stats from the MLB Stats API, ranks hitters by a
binomial hit-probability, and prints a ranked table. No formal issue tracker or PRD pipeline
is in use on this repo; changes land as direct feature-branch PRs.

## Key Files

- `main.py` — entry point. `--mode {subset,max,full}` selects the player pool/cap;
  `--cooldown-days N` (default 7) controls how long a no-data player is skipped before
  rechecking. Persists no-data results to `.cache/no_data_cache.json` (gitignored) between runs.
- `players.py` — the full `hitters` name -> slug pool (slug values are unused by the current
  API-based lookup, kept for reference/history).
- `test_suite.py` — pytest, parametrized, mocks `requests.get` — never hits the live API.
- `CHANGELOG.md` — Keep a Changelog format, dated entries (no version numbers).
- `skills/` — reusable patterns discovered on this repo (see `skills/skills.md`).

## Decisions (dated)

- **2026-07-11** — Replaced the retired Baseball-Reference HTML scraper with the official
  MLB Stats API (`statsapi.mlb.com`), eliminating scraping/bot-blocking risk entirely.
- **2026-07-11** — Guarded `scrape_player_data` against an empty `stats` list in the API
  response (previously an uncaught `IndexError` killed the whole run).
- **2026-07-11** — Added the `--mode`/`--cooldown-days` run-config toggle and the persisted
  no-data cache (see `skills/rate-limit-cooldown-cache.md` for the reusable pattern).
- **2026-07-11** — Left two pre-existing `main` commits authored by `copilot-swe-agent[bot]`
  (`52f51d9`, `e5bb3c2`) as-is after asking the user; rewriting shared `main` history was
  judged not worth the disruption. All commits since are authored by the repo owner.

## Known Blockers / Risks

- `AGENTS/RULES.md` §6 expects every PR to reference an issue; this repo has no issue
  tracker in use, so recent PRs omit that footer. Not a blocker, just a standing deviation.
- `config.ini` / `config.ini.example` are dead files (left over from the retired scraper) —
  not yet removed.

## Next Steps

1. Decide whether to remove the dead `config.ini` / `config.ini.example` files.
2. Decide whether `--mode max` should get its own cap independent of `--mode subset`'s
   `MAX_PLAYERS`, if real usage shows they need to diverge.
