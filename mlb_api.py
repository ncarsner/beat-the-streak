"""Shared MLB Stats API configuration.

Its own module because both the run path (`main.py`) and the calculator fetch
layer (`calculators.sources`) hit the same host, and a base URL duplicated in
two places is a base URL that eventually disagrees with itself.
"""

# MLB Stats API — official, free JSON API; no scraping, no bot-blocking
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
