"""Generate the static ballpark geometry table Category 5 reads as a constant.

Run on demand, never during a daily run or a test:

    uv run python -m scripts.generate_ballparks

Writes `calculators/data/ballparks.json`: elevation, roof type, playing surface,
and the five published fence distances for all 30 venues, keyed by MLB venue id.

Why a checked-in table rather than a fetch
------------------------------------------
Two reasons, and the second is the one that decides it. Park geometry changes
when a stadium is renovated, so a per-run fetch pays 30 requests for data whose
half-life is years. More importantly `CALC_33` needs a *league mean* fence
distance per sector to say whether today's park is deep or shallow, which means
all 30 venues are needed to evaluate any one of them. That is the `teams.py`
crosswalk pattern: static reference data, hand-regenerated, auditable in git.

What the API does and does not publish
--------------------------------------
`GET /venues/{id}?hydrate=location,fieldInfo,timezone` carries `location.elevation`,
`location.azimuthAngle` (the compass bearing from home plate to center field),
`location.defaultCoordinates`, `timeZone`, `fieldInfo.roofType`,
`fieldInfo.turfType`, and five fence distances. It does **not** publish wall
*heights*, which is why `CALC_33` is a distance-only geometry match: the Green
Monster reads as a 310-foot left-field line here, with nothing to say it is 37
feet tall.

Coordinates and time zone exist for Category 10: `CALC_70` measures travel as a
great-circle distance between consecutive venues and the body-clock cost as the
change in UTC offset. Both are additive to what Category 5 reads, so
regenerating this file does not disturb `CALC_33`, `CALC_35`, or `CALC_39`.

Measured 2026-08-09 across all 30 venues: every field above was populated, roof
types were {Open, Retractable, Dome}, and the sector means were LL 332.4,
LC 385.3, CF 404.3, RC 382.0, RL 328.6 feet.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import requests

from mlb_api import MLB_API_BASE

OUTPUT_PATH = pathlib.Path("calculators/data/ballparks.json")

# The five fence distances the API publishes, in left-to-right field order. The
# order is load-bearing: `calculators.category_05_ballpark_environment` maps
# spray-angle sectors onto these by position, not by name.
FENCE_KEYS = ("leftLine", "leftCenter", "center", "rightCenter", "rightLine")


def venue_ids(season: int) -> list[int]:
    """Return the home venue id of every active MLB club."""
    resp = requests.get(
        f"{MLB_API_BASE}/teams",
        params={"sportId": 1, "season": season},
        timeout=60,
    )
    resp.raise_for_status()
    return sorted({team["venue"]["id"] for team in resp.json().get("teams", [])})


def fetch_venue(venue_id: int) -> dict | None:
    """Return one venue's geometry record, or None when a field set is missing.

    A venue missing any fence distance is dropped rather than partially kept: a
    league mean computed over a table where some parks contribute three sectors
    and others five is not a league mean, and the gap would be invisible
    downstream.
    """
    resp = requests.get(
        f"{MLB_API_BASE}/venues/{venue_id}",
        params={"hydrate": "location,fieldInfo,timezone"},
        timeout=60,
    )
    resp.raise_for_status()
    venues = resp.json().get("venues") or []
    if not venues:
        return None

    venue = venues[0]
    field = venue.get("fieldInfo") or {}
    location = venue.get("location") or {}
    coordinates = location.get("defaultCoordinates") or {}
    timezone = venue.get("timeZone") or {}
    fences = [field.get(key) for key in FENCE_KEYS]
    if any(distance is None for distance in fences):
        print(f"  skipping venue {venue_id} ({venue.get('name')}): incomplete fences")
        return None

    return {
        "name": venue.get("name"),
        "elevation_ft": location.get("elevation"),
        "azimuth_degrees": location.get("azimuthAngle"),
        "latitude": coordinates.get("latitude"),
        "longitude": coordinates.get("longitude"),
        "timezone_id": timezone.get("id"),
        "utc_offset_hours": timezone.get("offset"),
        "roof_type": field.get("roofType"),
        "turf_type": field.get("turfType"),
        "fences_ft": [int(distance) for distance in fences],
    }


def main(season: int | None = None) -> dict:
    season = season or date.today().year
    venues = {}
    for venue_id in venue_ids(season):
        record = fetch_venue(venue_id)
        if record is not None:
            venues[str(venue_id)] = record

    document = {
        "season": season,
        "generated": date.today().isoformat(),
        "source": "MLB Stats API /venues?hydrate=location,fieldInfo,timezone",
        "note": (
            "fences_ft is [leftLine, leftCenter, center, rightCenter, rightLine] "
            "in feet, matching the spray sectors CALC_33 divides the field into. "
            "Wall heights are not published by this endpoint and are absent here."
        ),
        "venues": venues,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, indent=2) + "\n")
    return document


if __name__ == "__main__":
    doc = main()
    print(f"wrote {OUTPUT_PATH} ({len(doc['venues'])} venues)")
    for index, key in enumerate(FENCE_KEYS):
        values = [v["fences_ft"][index] for v in doc["venues"].values()]
        print(f"  {key:12} mean {sum(values) / len(values):.1f} ft")
