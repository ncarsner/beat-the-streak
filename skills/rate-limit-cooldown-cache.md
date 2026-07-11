# Skill: Rate-Limit Cooldown Cache

Skip re-polling entities that recently returned "no data" until a cooldown window elapses, using a small JSON file as the persistence layer between runs.

---

## Quick Reference

```python
import json
from datetime import datetime, timedelta
from pathlib import Path

CACHE_FILE = Path(__file__).parent / ".cache" / "no_data_cache.json"
DEFAULT_COOLDOWN_DAYS = 7


def load_cache(path=CACHE_FILE):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache, path=CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def is_in_cooldown(key, cache, cooldown_days):
    last_checked = cache.get(key)
    if not last_checked:
        return False
    last_checked_date = datetime.strptime(last_checked, "%Y-%m-%d")
    return (datetime.now() - last_checked_date) < timedelta(days=cooldown_days)
```

---

## Pattern: Cache on Miss, Clear on Hit

For each entity polled in a batch job:

1. Check `is_in_cooldown(key, cache, cooldown_days)` before making the request. If true, skip
   without hitting the network.
2. On a result that counts as "no data", write today's date into `cache[key]`.
3. On a result that counts as "data found", remove `cache.pop(key, None)` so a future miss
   restarts the cooldown window rather than compounding on stale state.
4. Persist the cache to disk once per run (load at start, save at end), not per entity, so a
   crash mid-run does not partially commit cache state.

Make the cooldown window a runtime parameter (CLI flag or function argument), not a hardcoded
constant, so it can be tuned without a code change when the underlying data's refresh rate
changes.

### When to use

- The upstream source is known to be slow-changing relative to the poll frequency (e.g. a
  player's game log won't meaningfully change within a day, but might within a week).
- Reducing request volume against a rate-limited or courtesy-limited API matters more than
  catching a rare early update.

### When not to use

- The upstream data can change faster than the cooldown window in ways the caller needs to
  observe promptly. A fixed cooldown will hide that update.
