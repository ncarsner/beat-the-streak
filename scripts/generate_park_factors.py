"""Generate the park-factor table CALC_31, CALC_32, and CALC_39 read as constants.

Run on demand, never during a daily run or a test:

    uv run python -m scripts.generate_park_factors

Writes `calculators/data/park_factors.json`: Statcast's 3-year rolling park
factor indexes per venue, for all batters and split by batter hand, plus the
roof-closed grouping.

A deliberate exception to the no-scraping decision, scoped to this script
--------------------------------------------------------------------------
The 2026-07-11 decision moved this project off HTML scraping and onto the MLB
Stats API. The Stats API publishes no park factors, and `pybaseball` 2.0.0 (the
one authorized scraping dependency) exposes no park-factor function -- its
`parks` and `park_codes` are Retrosheet identifiers, not factors. So the choice
was a one-off read of Savant's leaderboard page or shipping `CALC_31`, `CALC_32`,
and `CALC_39` as permanent `None`.

The exception is narrowed in three ways. It lives in `scripts/`, which never runs
during a daily run or a test. Its output is a checked-in JSON file, so the
calculators read a constant and the repo carries no scraper on any code path a
run touches. And the parse is of an embedded ``var data = [...]`` JSON array
rather than of markup, so it fails loudly on a page change instead of quietly
returning wrong numbers.

Regenerate periodically, not annually
-------------------------------------
`key_num_years_rolling` is 3 and `year_range` reads "2024-2026", so the window
**includes the in-progress season** and moves as games are played. This is a
snapshot of a moving quantity, not a stable annual constant, which is the whole
reason `generated` and `year_range` are written into the file: a stale table is
detectable rather than merely wrong.

Index convention: 100 is league-neutral, so a park factor of 104 means 4 percent
more of that outcome than a neutral park. The calculators divide by 100.

Conditions verified 2026-08-09: `All` returns 28 venues, `Roof Closed` returns 7,
`Day` and `Night` return 28 each. There is **no working `Roof Open` grouping** --
it returns zero rows -- which is why `CALC_39` compares the closed-roof index
against the same park's all-conditions index and documents the bias that
introduces rather than claiming a clean open-versus-closed delta.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import date

import requests

LEADERBOARD_URL = "https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
OUTPUT_PATH = pathlib.Path("calculators/data/park_factors.json")

# Savant returns a full HTML page; the leaderboard rows are the one embedded
# `var data = [...]` array. Anchored on the exact variable name so a second
# array elsewhere on the page cannot be picked up by accident.
DATA_PATTERN = re.compile(r"var\s+data\s*=\s*(\[.*?\]);", re.DOTALL)

# Savant serves a default page to unknown agents; a browser agent gets the
# leaderboard. Not evasion -- the data is public and unauthenticated -- just the
# header that makes the endpoint return its own content.
HEADERS = {"User-Agent": "Mozilla/5.0"}

# The index columns kept. `index_hits` is CALC_31's headline value; the
# component 1B/2B/3B indexes are kept because the ROADMAP names them and a
# future consumer may want singles weighted differently from doubles. `index_hr`
# is kept as context for CALC_33's geometry match.
INDEX_KEYS = ("index_hits", "index_1b", "index_2b", "index_3b", "index_hr")

# Batter-hand groupings. The empty string is Savant's "all batters" value.
BAT_SIDES = {"All": "", "L": "L", "R": "R"}

YEARS_ROLLING = "3"


def parse_leaderboard(page: str) -> list[dict]:
    """Extract the embedded leaderboard rows from a Savant page.

    Raises rather than returning empty on a page whose shape changed: this is a
    generator run by hand, and a silent empty table would be written to disk and
    read as "every park is neutral" by every calculator downstream.
    """
    match = DATA_PATTERN.search(page)
    if match is None:
        raise ValueError("Savant park-factor page carried no `var data` array")
    rows = json.loads(match.group(1))
    if not rows:
        raise ValueError("Savant park-factor page returned zero rows")
    return rows


def fetch_grouping(year: int, bat_side: str, condition: str) -> list[dict]:
    """Return the leaderboard rows for one (batter hand, condition) grouping."""
    resp = requests.get(
        LEADERBOARD_URL,
        params={
            "type": "year",
            "year": str(year),
            "batSide": bat_side,
            "stat": "index_wOBA",
            "condition": condition,
            "rolling": YEARS_ROLLING,
            "sort": "0",
            "sortDir": "desc",
        },
        headers=HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    return parse_leaderboard(resp.text)


def _indexes(row: dict) -> dict:
    """Project one leaderboard row to the fields the calculators read.

    Savant serves every numeric as a string. They are coerced here rather than
    at read time so a malformed value fails during generation, where a human is
    watching, instead of inside a calculator.
    """
    record = {"n_pa": int(row["n_pa"])}
    for key in INDEX_KEYS:
        value = row.get(key)
        record[key] = int(value) if value not in (None, "") else None
    return record


def collect(year: int) -> tuple[dict, str]:
    """Build the venue table across every grouping, and the rolling year range."""
    venues: dict[str, dict] = {}
    year_range = ""

    groupings = [(label, side, "All") for label, side in BAT_SIDES.items()]
    groupings.append(("roof_closed", "", "Roof Closed"))

    for label, bat_side, condition in groupings:
        rows = fetch_grouping(year, bat_side, condition)
        print(f"  {label:12} condition={condition:12} {len(rows)} venues")
        for row in rows:
            year_range = row.get("year_range") or year_range
            venue = venues.setdefault(
                str(row["venue_id"]),
                {"name": row.get("venue_name"), "groupings": {}},
            )
            venue["groupings"][label] = _indexes(row)

    return venues, year_range


def main(year: int | None = None) -> dict:
    year = year or date.today().year
    venues, year_range = collect(year)

    document = {
        "year": year,
        "year_range": year_range,
        "years_rolling": int(YEARS_ROLLING),
        "generated": date.today().isoformat(),
        "source": f"{LEADERBOARD_URL} (embedded leaderboard JSON)",
        "note": (
            "Index convention: 100 is league-neutral. Keyed by MLB venue id, "
            "which Savant's venue_id matches. The rolling window includes the "
            "in-progress season, so this is a snapshot of a moving quantity: "
            "regenerate periodically, and read `year_range` before trusting it. "
            "The `roof_closed` grouping covers only retractable and domed parks; "
            "Savant publishes no working roof-open grouping."
        ),
        "venues": venues,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n")
    return document


if __name__ == "__main__":
    doc = main()
    print(f"wrote {OUTPUT_PATH} ({len(doc['venues'])} venues, {doc['year_range']})")
