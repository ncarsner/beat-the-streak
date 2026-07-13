"""MLB team name to ID and abbreviation crosswalk, keyed by exact API name strings."""

from typing import TypedDict


class TeamInfo(TypedDict):
    id: int
    abbreviation: str


TEAM_CROSSWALK: dict[str, TeamInfo] = {
    "Arizona Diamondbacks": {"id": 109, "abbreviation": "AZ"},
    "Athletics": {"id": 133, "abbreviation": "ATH"},
    "Atlanta Braves": {"id": 144, "abbreviation": "ATL"},
    "Baltimore Orioles": {"id": 110, "abbreviation": "BAL"},
    "Boston Red Sox": {"id": 111, "abbreviation": "BOS"},
    "Chicago Cubs": {"id": 112, "abbreviation": "CHC"},
    "Chicago White Sox": {"id": 145, "abbreviation": "CWS"},
    "Cincinnati Reds": {"id": 113, "abbreviation": "CIN"},
    "Cleveland Guardians": {"id": 114, "abbreviation": "CLE"},
    "Colorado Rockies": {"id": 115, "abbreviation": "COL"},
    "Detroit Tigers": {"id": 116, "abbreviation": "DET"},
    "Houston Astros": {"id": 117, "abbreviation": "HOU"},
    "Kansas City Royals": {"id": 118, "abbreviation": "KC"},
    "Los Angeles Angels": {"id": 108, "abbreviation": "LAA"},
    "Los Angeles Dodgers": {"id": 119, "abbreviation": "LAD"},
    "Miami Marlins": {"id": 146, "abbreviation": "MIA"},
    "Milwaukee Brewers": {"id": 158, "abbreviation": "MIL"},
    "Minnesota Twins": {"id": 142, "abbreviation": "MIN"},
    "New York Mets": {"id": 121, "abbreviation": "NYM"},
    "New York Yankees": {"id": 147, "abbreviation": "NYY"},
    "Philadelphia Phillies": {"id": 143, "abbreviation": "PHI"},
    "Pittsburgh Pirates": {"id": 134, "abbreviation": "PIT"},
    "San Diego Padres": {"id": 135, "abbreviation": "SD"},
    "San Francisco Giants": {"id": 137, "abbreviation": "SF"},
    "Seattle Mariners": {"id": 136, "abbreviation": "SEA"},
    "St. Louis Cardinals": {"id": 138, "abbreviation": "STL"},
    "Tampa Bay Rays": {"id": 139, "abbreviation": "TB"},
    "Texas Rangers": {"id": 140, "abbreviation": "TEX"},
    "Toronto Blue Jays": {"id": 141, "abbreviation": "TOR"},
    "Washington Nationals": {"id": 120, "abbreviation": "WSH"},
}
