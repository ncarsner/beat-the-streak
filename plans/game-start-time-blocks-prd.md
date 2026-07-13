# Compute each player's game start hour for future block-based grouping

## Overview

Player data currently carries no information about when that player's game
starts, which blocks any future ability to group players by game start-time
block. This adds a raw UTC start-hour value to each player's compiled data,
sourced from a single day-wide MLB Stats API schedule fetch, laying the
groundwork for a later recurring-schedule SMS feature without changing any
current visible output.

## Goals

- Every player whose team has a normally-scheduled game today gets a raw UTC
  start hour attached to their compiled data.
- Computing this requires exactly one additional MLB Stats API request per
  run, regardless of player pool size or `--mode`.
- A failure to fetch the schedule degrades the run gracefully — no player's
  stats, probability, or Team data is affected, and the failure is visible
  both immediately (console) and durably (a persisted log).
- No change to the printed table or any other current user-visible output.

## Non-goals

- Displaying game start time or block membership in the printed table (this
  phase attaches data only; display is a future concern).
- Any fixed or dynamically-clustered "block" boundary definition (e.g. named
  ranges like "afternoon"/"evening") — the value stored is the raw start
  hour itself, not a bucketed block label.
- Timezone conversion to US/Eastern or any subscriber's local time — the
  stored value is raw UTC; conversion is deferred to whichever future phase
  actually displays or sends it to a user.
- The recurring/scheduled SMS-sending pipeline itself, or any "filter
  players by block" logic that would consume this data — this phase only
  produces the data point.
- Representing a team's second game of a doubleheader — only the first game
  (`gameNumber == 1`) is used.
- Handling non-"Postponed" anomalous statuses (e.g. suspended, rain-delayed
  mid-game, fully cancelled) — only `"Postponed"` is excluded, per what was
  explicitly verified against the live API and discussed during design (see
  Open questions).

## User stories

- As the maintainer, I want each player's compiled data to carry their raw
  game start hour so that a future phase can group or filter players by
  when their game starts without re-deriving this from scratch.
- As the maintainer, I want a schedule-fetch failure to degrade gracefully
  and log clearly (console + file) so that a bad network call never takes
  down the tool's core ranking output, and I can still tell after the fact
  that it happened.

## Requirements

1. Add `fetch_schedule(date)`, issuing one request to
   `/schedule?sportId=1&date=<date>`, returning a `{team_id: game_hour_utc}`
   dict for that date. For a team with multiple games that date (a
   doubleheader), only the game with `gameNumber == 1` is included. A game
   whose `status.detailedState` is `"Postponed"` is excluded from the map
   entirely (that team gets no entry for the date).
2. On a `fetch_schedule` request failure (`requests.RequestException`),
   degrade gracefully: print an error message to console (matching the
   existing per-player failure convention already in `scrape_player_data`),
   append a line to `.cache/schedule_fetch_errors.log` formatted as
   `YYYY-MM-DD HH:MM:SS — <exception message>` (appended, never truncated),
   and return an empty dict so the rest of the run proceeds unaffected.
3. `scrape_player_data` accepts a new `schedule_map` parameter and attaches
   `"GameHourUTC"` to the returned player data dict: the mapped raw UTC hour
   (an `int`, 0-23) when the player's crosswalk-resolved `team_id` is a key
   in `schedule_map`, else `None`.
4. `compile_player_data` accepts and threads `schedule_map` through to each
   `scrape_player_data` call, mirroring how `missing_team_cache` is already
   threaded through.
5. `__main__` calls `fetch_schedule` once per run (today's date) before
   compiling player data, and passes the resulting map into
   `compile_player_data`. No caching or persistence of the schedule map
   itself across runs — it is fetched fresh every run.

## Acceptance criteria

- [ ] `fetch_schedule(date)` makes exactly one HTTP request and returns
      `{team_id: int_hour}` for that date's normally-scheduled games.
- [ ] A team with a doubleheader that date appears once in the map, keyed to
      its `gameNumber == 1` game's hour.
- [ ] A team whose only game that date has `status.detailedState ==
      "Postponed"` does not appear in the map.
- [ ] A `requests.RequestException` during `fetch_schedule` results in: a
      console-printed error message, a new line appended to
      `.cache/schedule_fetch_errors.log`, and an empty dict returned — no
      exception propagates out of the function.
- [ ] A player whose crosswalk-resolved `team_id` is a key in `schedule_map`
      gets `"GameHourUTC"` set to that mapped hour.
- [ ] A player whose `team_id` is unavailable (unresolved crosswalk, no
      `currentTeam`) or not present in `schedule_map` (off day, excluded
      postponed game) gets `"GameHourUTC"` set to `None`.
- [ ] The printed table's columns and content are unchanged from the
      current (post `team-abbr-display`) output — no new column, no
      formatting change.
- [ ] Existing tests (stats, probability, Team column, missing-team cache)
      continue to pass unmodified in behavior, only extended with new
      `schedule_map`/`GameHourUTC` coverage.

## Open questions

- Whether statuses other than `"Postponed"` (suspended, rain delay,
  cancelled) should also exclude a game from the map was not resolved —
  scoped out of this phase per the non-goals above; only `"Postponed"` was
  verified live and discussed.
- The eventual US/Eastern (and later per-subscriber local time) conversion
  step, and where in the codebase it should live, is explicitly deferred to
  the future SMS phase and was not designed here.
- Whether `GameHourUTC` should ever be surfaced in the printed table is
  deferred — this phase deliberately keeps it data-only.
