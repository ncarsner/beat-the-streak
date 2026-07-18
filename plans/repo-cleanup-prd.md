# Repo cleanup: README accuracy, dead config files, test coverage gaps

## Overview

Three independent inaccuracies/gaps accumulated as the tool evolved from an HTML scraper
to the MLB Stats API: the README understates the real Python floor, two scraper-era
config files remain despite the API needing no configuration, and the newest code
(the schedule-fetch feature) has no test coverage. This PRD scopes the fix for all three
as independently deliverable, separately tracked issues.

## Goals

- README's stated Python floor matches what the code actually requires.
- No dead configuration files remain in the repo once the MLB API path needs none.
- `fetch_schedule`, `log_schedule_fetch_error`, `probable_hitters`, and `build_arg_parser`
  each have direct test coverage.
- Each of the three fixes ships as its own issue/PR, independently of the others.

## Non-goals

- Pinning versions in `requirements.txt` — explicitly deferred, not in scope for this PRD.
- Any refactor to restore genuine Python 3.9 support (e.g. `from __future__ import
  annotations`) — the decision made was to correct the documented floor to match the
  code, not to widen the code's actual compatibility.
- Unit-testing the `if __name__ == "__main__":` block directly — out of scope unless an
  existing smoke-test pattern is found in `test_suite.py` to extend (verify before
  assuming one exists).
- Any change to `.gitignore` beyond removing the now-unnecessary `config.ini` line, and
  only after confirming no other untracked local file still needs that rule.

## User stories

- As a new contributor, I want the README's Python version claim to match reality so
  that I don't hit a `TypeError` on import following the documented setup steps.
- As a maintainer, I want no leftover scraper-era config files in the repo so that a
  reader isn't misled into thinking the MLB API path requires configuration.
- As a maintainer, I want the newest schedule-fetch code under test so that future
  changes to it have a regression net, matching the coverage bar already set for the
  rest of `main.py`.

## Requirements

1. Change the README's Installation section from "Python 3.9+" to "Python 3.10+",
   matching the actual floor set by `main.py`'s `dict | None` annotation usage (which,
   absent `from __future__ import annotations`, requires 3.10+ at runtime).
2. Delete `config.ini.example` (tracked) and `config.ini` (local, gitignored); remove the
   now-unnecessary `config.ini` line from `.gitignore` (`.gitignore:1`) once confirmed no
   other untracked file still depends on that ignore rule.
3. Add test coverage in `test_suite.py` for:
   - `fetch_schedule`: happy path, doubleheader `gameNumber == 1` selection, `"Postponed"`
     game exclusion, empty/absent `dates` key.
   - `log_schedule_fetch_error`: creates parent dirs if absent, appends rather than
     truncates on repeated calls.
   - `probable_hitters`: correct top-`n`/bottom-`n` slicing behavior.
   - `build_arg_parser`: default `--mode`/`--cooldown-days` values, and that explicit
     flags override the defaults.

## Acceptance criteria

- [ ] README's Installation section reads "Python 3.10+" (or equivalent phrasing) instead
      of "Python 3.9+".
- [ ] `config.ini.example` no longer exists in the repository (tracked deletion).
- [ ] `config.ini` no longer exists on disk.
- [ ] `.gitignore` no longer references `config.ini`, provided no other untracked file
      needs that rule.
- [ ] `test_suite.py` has passing tests exercising `fetch_schedule`'s doubleheader
      selection and postponed-game exclusion behavior.
- [ ] `test_suite.py` has a passing test confirming `log_schedule_fetch_error` appends
      (not truncates) on a second call and creates missing parent directories.
- [ ] `test_suite.py` has a passing test confirming `probable_hitters` returns the
      correct top-`n` and bottom-`n` slices.
- [ ] `test_suite.py` has a passing test confirming `build_arg_parser` defaults and
      override behavior for `--mode` and `--cooldown-days`.
- [ ] `pytest test_suite.py -v` passes in full after all three changes land.

## Open questions

- Whether the `__main__` block itself should get any test coverage (e.g. via a
  subprocess smoke test) was not resolved — scoped out above pending a check of whether
  `test_suite.py` already has a comparable pattern to extend.
- Whether removing the `config.ini` line from `.gitignore` could affect any other
  untracked local file was not verified here — left as a check for whoever implements
  the dead-config-file issue.
