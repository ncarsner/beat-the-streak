# Display player team abbreviation in ranked output

## Overview

The ranked hitter table currently shows only name, hit/at-bat line, walk/strikeout
line, and hit probability, with no indication of which team each player is on.
This adds a "Team" column showing each player's current 3-letter team
abbreviation, sourced from a maintainer-controlled static crosswalk, laying
groundwork for the later game-start-time-block feature (which needs team id).

## Goals

- Every player row in the ranked table shows a 3-letter team abbreviation when
  the crosswalk has an entry for their current team.
- Adding the "Team" column requires zero additional MLB Stats API requests
  beyond what `lookup_player_id` already makes per player.
- Crosswalk gaps (a team name the API returns that isn't yet in `teams.py`)
  are discoverable from a persisted file without re-reading console scrollback.
- The crosswalk schema anticipates the next phase (game-start-time blocks) by
  storing team `id` alongside `abbreviation`, avoiding a second migration.

## Non-goals

- Live-fetching the abbreviation from the MLB Stats API's `/teams` endpoint
  at runtime (explicitly rejected in favor of a maintainer-controlled static
  crosswalk).
- Game start-time batching, schedule lookups, or any SMS/notification work
  (separate future phase, out of scope here).
- Automatic crosswalk maintenance or team-name fuzzy matching — gaps are
  surfaced for manual review, not auto-resolved.
- Historical/traded-team display — only `currentTeam` is ever shown, never
  the team a player was on for a specific past game in the log window.

## User stories

- As a tool user, I want to see each hitter's current team next to their name
  so I can quickly scan the ranked list without cross-referencing rosters
  elsewhere.
- As the maintainer, I want unmapped team names flagged in a persisted file
  so I can update the crosswalk without re-running the tool to rediscover
  gaps that scrolled past in a prior run.
- As the maintainer, I want stale review-cache entries to clear automatically
  once I fix the crosswalk, so the file doesn't accumulate cruft I have to
  hand-prune.

## Requirements

1. `lookup_player_id` is replaced by `lookup_player_info`, adding
   `hydrate=currentTeam` to the existing `/people/search` request. It
   returns and caches `{id, team_name}` per player per run (previously bare
   `id`).
2. A new `teams.py` file defines a crosswalk dict, `name -> {"id": int,
   "abbreviation": str}`, seeded from a live one-time pull of
   `/teams?sportId=1` (30 entries) as keys, with abbreviation values
   supplied/confirmed by the maintainer.
3. Each player's compiled data dict gains a `"Team"` field, resolved by
   looking up `team_name` (from requirement 1) in the `teams.py` crosswalk.
4. The `PrettyTable` built in `probable_hitters` gains a `"Team"` column,
   positioned immediately after `"Player"`.
5. When `team_name` is present but absent from the `teams.py` crosswalk
   (case a), the player's `"Team"` cell is blank, and an entry is written to
   `.cache/missing_team_cache.json` as
   `{team_name: {"first_seen": "YYYY-MM-DD", "players": [names]}}`,
   appending to the `players` list on repeat encounters. No console output
   is produced for this case.
6. When a player has no `currentTeam` at all in the API response (case b),
   the `"Team"` cell is blank and no entry is written to
   `missing_team_cache.json`.
7. When a `team_name` previously recorded in `missing_team_cache.json`
   successfully resolves against the crosswalk on a later run (maintainer
   added the abbreviation), that entry is removed from the cache file
   during that run.

## Acceptance criteria

- [ ] `lookup_player_info(name)` returns `{id, team_name}` (or equivalent)
      using one `/people/search` request with `hydrate=currentTeam`; no
      second request is made to resolve team.
- [ ] `teams.py` exists with a `name -> {"id", "abbreviation"}` dict
      containing all 30 current MLB team names as keys.
- [ ] A player whose `currentTeam.name` matches a `teams.py` key shows the
      correct abbreviation in the `"Team"` column.
- [ ] The `"Team"` column appears directly after `"Player"` in the printed
      table.
- [ ] A player whose `currentTeam.name` is not in `teams.py` shows a blank
      `"Team"` cell and produces a `missing_team_cache.json` entry with
      `first_seen` date and their name in `players`; running again with the
      same gap appends their name without a duplicate top-level entry; no
      console output is printed for this case.
- [ ] A player with no `currentTeam` in the API response shows a blank
      `"Team"` cell and produces no `missing_team_cache.json` entry.
- [ ] After adding a previously-missing team's abbreviation to `teams.py`,
      the next run removes that team's entry from
      `missing_team_cache.json`.

## Open questions

- The 25 remaining team abbreviation values are not yet supplied (5 sampled
  live during `/grill-me`: `ATH`, `PIT`, `SD`, `SEA`, `SF`) — maintainer must
  fill in the rest of `teams.py` before this ships.
- Whether `missing_team_cache.json` reuses a generalized version of the
  existing `load_no_data_cache`/`save_no_data_cache` helpers, or gets
  dedicated load/save functions, is an implementation detail left to
  `/prd-to-issues` / development, not a resolved design decision.
