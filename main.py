import argparse
import json
import os
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from prettytable import PrettyTable
from time import sleep
from zoneinfo import ZoneInfo
import random

from calculators.category_07_bullpen_exposure import (
    OPENER_MAX_BF_PER_START,
    RELIEF_BF_SHARE_MIN,
    relief_share,
    start_lines,
)
from calculators.sources.category_07_bullpen_exposure import fetch_pitching_game_logs
from mlb_api import MLB_API_BASE
from teams import TEAM_ID_TO_ABBR


# Limit the number of players fetched per run for validation purposes.
# Increase or set to None to process all players in the provided pool.
MAX_PLAYERS = 50

# Where "no recent data" results are remembered between runs.
NO_DATA_CACHE_FILE = Path(__file__).parent / ".cache" / "no_data_cache.json"

# Where per-game lineup queries are recorded to avoid re-querying on the same day.
QUERIED_GAMES_CACHE_FILE = Path(__file__).parent / ".cache" / "queried_games_cache.json"

# Where successfully-sent SMS groupings are recorded to avoid re-sending on the same day.
SMS_SENT_CACHE_FILE = Path(__file__).parent / ".cache" / "sms_sent_cache.json"

# Where per-grouping email sends are recorded. Separate from the SMS cache on
# purpose: the two channels send independently, so a grouping already texted
# must still be emailable, and vice versa. Sharing one file would make whichever
# channel ran first suppress the other.
EMAIL_SENT_CACHE_FILE = Path(__file__).parent / ".cache" / "email_sent_cache.json"

# Where /schedule request failures are logged for later review.
SCHEDULE_ERROR_LOG_FILE = Path(__file__).parent / ".cache" / "schedule_fetch_errors.log"

# Days a player stays skipped after polling with no recent data — a fresh
# game log won't have accumulated in less time than this. Overridable per
# run via `--cooldown-days`.
DEFAULT_COOLDOWN_DAYS = 7

# The zone the slate date is resolved in, never the host clock.
#
# The MLB `date` parameter selects on `officialDate`, which is venue-local: a
# game starting 01:50 UTC at T-Mobile Park carries the previous calendar day
# (verified 2026-08-08). The scheduled runner is UTC, so between 00:00 and 03:00
# UTC its own date is already tomorrow and every one of those firings requests a
# slate that has not happened yet. Those firings exist precisely to cover late
# West Coast starts, so they are the ones that must not be wrong, and the failure
# is silent: `select_games` simply returns empty, which is also what a legitimate
# quiet period looks like. It also splits the queried-games cache across the day
# boundary, so the 23:45 firing's work is never reused by the 00:00 one.
#
# No single zone is correct for every venue, but Eastern is the one that makes
# the boundary land in the right place: it is the latest US zone, so it rolls
# over after every venue in the country has finished the day's games.
SLATE_TIMEZONE = ZoneInfo("America/New_York")


def env_setting(name: str) -> str:
    """Return environment variable *name* with surrounding whitespace removed.

    Every credential and endpoint this module reads goes through here, because
    CI manufactures two values that a plain `os.environ.get` reads as real
    configuration:

    1. An **unset** GitHub Actions secret interpolates to an empty string rather
       than being absent, so the variable is present and a `get` default never
       fires. This shipped: the first live scheduled run reported success and
       sent nothing, because `SMTP_PORT` was unset, arrived as `""`, and
       `int("")` raised into the skip path.
    2. A secret set with `gh secret set X < file` or `echo v | gh secret set X`
       carries the **trailing newline**. Nothing displays it, the value looks
       right in every UI, and the provider rejects it as a bad credential.

    Both are "not configured" wearing the costume of a configured value.
    Stripping is deliberately surrounding-only: an interior space can be a
    legitimate part of a passphrase, and silently deleting it would break a
    working credential to fix a broken one.
    """
    return os.environ.get(name, "").strip()


def slate_date(now: datetime | None = None) -> datetime:
    """Return *now* as a `SLATE_TIMEZONE` datetime, the day the slate belongs to.

    Every date a run derives, the `/schedule` query, the cache keys, the sent-cache
    keys and the printed table title, has to come from this one clock. Mixing it
    with the host's would let a cache be written under one date and read under
    another on the same run.

    *now* must be timezone-aware; a naive value cannot be converted without
    assuming the host zone, which is the whole defect.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return now.astimezone(SLATE_TIMEZONE)


def load_no_data_cache(path=NO_DATA_CACHE_FILE):
    """Return the {player: last_checked_date_str} cache from a prior run, or {} if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_no_data_cache(cache, path=NO_DATA_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def _json_encode_datetime(value):
    """Serialize a datetime as an ISO 8601 string for `json.dump`'s *default*.

    Cached player records carry `start_dt`, which the model needs as a real
    `datetime` (CALC_70 resolves each venue's IANA zone at that instant), so the
    cache is the only place the type has to flatten. Anything else unserializable
    still raises, which is the behaviour we want: silently stringifying an
    unexpected object would put a value in the cache that `load` cannot restore.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def load_queried_games_cache(path=QUERIED_GAMES_CACHE_FILE):
    """Return the {gamePk_str: {"date": ..., "players": [...]}} cache from a prior run, or {} if absent/corrupt.

    `start_dt` is restored to an aware `datetime`, inverting the ISO encoding
    `save_queried_games_cache` applies. The round trip has to preserve tzinfo:
    `main.fetch_schedule` builds the value aware, and a naive one silently moves
    CALC_70's timezone-shift answer instead of failing.

    A player record written before `start_dt` existed simply has no such key and
    is left alone. An unparseable timestamp invalidates the whole cache, which
    costs one lineup re-fetch and is preferable to handing the model a field it
    cannot use.
    """
    try:
        with open(path) as f:
            cache = json.load(f)
        for entry in cache.values():
            for player in entry.get("players", []):
                if player.get("start_dt") is not None:
                    player["start_dt"] = datetime.fromisoformat(player["start_dt"])
        return cache
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return {}


def save_queried_games_cache(cache, path=QUERIED_GAMES_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True, default=_json_encode_datetime)


def load_sms_sent_cache(path=SMS_SENT_CACHE_FILE):
    """Return the {game_hour_str: date_str} cache from a prior run, or {} if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_sms_sent_cache(cache, path=SMS_SENT_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def load_email_sent_cache(path=EMAIL_SENT_CACHE_FILE):
    """Return the {game_hour_str: date_str} cache from a prior run, or {} if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_email_sent_cache(cache, path=EMAIL_SENT_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def is_sms_sent_today(game_hour_utc: int, cache: dict, today: str) -> bool:
    """True if a notification for *game_hour_utc* was already sent on *today*.

    Channel-agnostic: both the SMS and the email dispatchers call this against
    their own cache file, so the predicate is the same and the state is not.
    """
    return cache.get(str(game_hour_utc)) == today


def is_game_queried_today(game_pk: int, cache: dict, today: str) -> bool:
    """True if *game_pk* was already queried on *today*."""
    entry = cache.get(str(game_pk))
    return isinstance(entry, dict) and entry.get("date") == today


def log_schedule_fetch_error(exc, path=SCHEDULE_ERROR_LOG_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a") as f:
        f.write(f"{timestamp} — {exc}\n")


def probable_pitcher_id(game: dict, side: str) -> int | None:
    """Return the probable starter's player id for *side*, or None if unannounced."""
    pitcher = game.get("teams", {}).get(side, {}).get("probablePitcher")
    return pitcher.get("id") if pitcher else None


def fetch_schedule(date: str) -> list[dict]:
    """Return per-game records for all non-postponed games on *date* (YYYY-MM-DD).

    Each record: {gamePk, gameNumber, home_team_id, away_team_id, start_dt,
    abstract_state, home_pitcher_id, away_pitcher_id}. Both games of a
    doubleheader appear as distinct entries. Probable pitcher ids are None until
    the team announces a starter, and stay None for an opener the API does not
    list.

    `abstract_state` is the schedule's `abstractGameState`: "Preview" until first
    pitch, then "Live", then "Final". It is what `select_games` uses to decide
    whether a game has begun, since a scheduled start time is a plan and a status
    is an observation.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "date": date, "hydrate": "probablePitcher"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"connection error ({exc})")
        log_schedule_fetch_error(exc)
        return []
    dates = resp.json().get("dates", [])
    if not dates:
        return []
    schedule = []
    for game in dates[0].get("games", []):
        if game.get("status", {}).get("detailedState") == "Postponed":
            continue
        start_dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
        schedule.append(
            {
                "gamePk": game["gamePk"],
                "gameNumber": game.get("gameNumber", 1),
                "home_team_id": game["teams"]["home"]["team"]["id"],
                "away_team_id": game["teams"]["away"]["team"]["id"],
                "start_dt": start_dt,
                "abstract_state": game.get("status", {}).get("abstractGameState"),
                "home_pitcher_id": probable_pitcher_id(game, "home"),
                "away_pitcher_id": probable_pitcher_id(game, "away"),
            }
        )
    return schedule


def batting_order_spot(raw: object) -> int | None:
    """Return the 1-9 lineup spot from a boxscore `battingOrder` value.

    The boxscore encodes the spot as a 3-digit string: "100" is the starter in
    the first spot, "101" the first substitute to bat there, "900" the ninth
    spot. Integer-dividing by 100 recovers the spot and collapses substitutes
    onto the spot they inherited, which is the thing that projects plate
    appearances.

    Returns None for a missing, non-numeric, or out-of-range value rather than
    raising or extrapolating — a lineup entry with a spot of 0 or 11 is a shape
    this code does not understand, and guessing would be worse than abstaining.
    """
    try:
        spot = int(raw) // 100
    except (TypeError, ValueError):
        return None
    return spot if 1 <= spot <= 9 else None


def fetch_lineup(game_pk: int) -> dict[str, list[dict]]:
    """Return posted batting-order players for *game_pk* keyed by "home" and "away".

    Each player dict: {id, fullName, team_id, lineup_spot}. A team with an empty
    or absent battingOrder returns an empty list for that side. Returns
    {"home": [], "away": []} on request failure without raising.

    `lineup_spot` is additive and unused by the run: it feeds CALC_41, which is
    built but not wired, so the ranked table is unchanged.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/game/{game_pk}/boxscore",
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"lineup fetch error for gamePk={game_pk} ({exc})")
        log_schedule_fetch_error(exc)
        return {"home": [], "away": []}

    teams_data = resp.json().get("teams", {})
    result: dict[str, list[dict]] = {}
    for side in ("home", "away"):
        team = teams_data.get(side, {})
        batting_order = team.get("battingOrder") or []
        team_players = team.get("players", {})
        side_list = []
        for player_id in batting_order:
            info = team_players.get(f"ID{player_id}", {})
            side_list.append(
                {
                    "id": player_id,
                    "fullName": info.get("person", {}).get("fullName", ""),
                    "team_id": info.get("parentTeamId"),
                    "lineup_spot": batting_order_spot(info.get("battingOrder")),
                }
            )
        result[side] = side_list
    return result


def has_started(game: dict, now: datetime) -> bool:
    """Whether *game* has already begun, and its hitters are therefore ineligible.

    A pick can only be made on a game that has not started, so this is the
    eligibility test, not a convenience filter.

    The schedule's `abstract_state` is authoritative when present, because a
    start time is a plan and a status is an observation, and the two disagree in
    both directions: a rain-delayed game sits at "Preview" well past its
    scheduled first pitch and its hitters are still perfectly eligible, while a
    resumed suspended game reports "Live" against a start time that may read as
    future. Falling back to the clock only when the field is absent keeps records
    that predate the field behaving exactly as they did.
    """
    state = game.get("abstract_state")
    if state is not None:
        return state != "Preview"
    return game["start_dt"] < now


def select_games(
    games: list[dict], now: datetime, scheduled: bool = False
) -> list[dict]:
    """Filter *games* to those relevant for this run.

    Both modes drop any game that has already begun (see `has_started`).

    Manual mode (scheduled=False): every game still to start.
    Scheduled mode (scheduled=True): games where 0 < start_dt - now <= 2 hours,
    sized for the cron cadence.
    """
    upcoming = [g for g in games if not has_started(g, now)]
    if scheduled:
        window = timedelta(hours=2)
        return [g for g in upcoming if timedelta(0) < g["start_dt"] - now <= window]
    return upcoming


def is_opener(games: list[dict]) -> bool:
    """Whether a pitcher's game log profiles him as an opener, not a starter.

    *games* is his season game log from
    `calculators.sources.category_07_bullpen_exposure.fetch_pitching_game_logs`.

    Two ways to qualify, and the second is why this is not simply `CALC_51`:

    1. He has started, and his starts have averaged no more than
       `OPENER_MAX_BF_PER_START` batters faced. This is `CALC_51`'s rule.
    2. He has **never** started and is a reliever by workload. `CALC_51` returns
       None here, correctly: with no starts there is no start length to measure,
       so as a *measurement* the answer is "no evidence". But as a *decision*,
       a reliever announced as today's starter is the clearest opener there is,
       and the highest-probability case of the thing this gate exists to catch.
       Keeping the two rules separate is deliberate. The calculator answers what
       was measured; the gate answers what to do.

    An empty log returns False, so a pitcher with no season history is treated as
    a conventional starter and his game is kept. That matches the flag's
    include-when-unknown policy; see `build_arg_parser`.
    """
    if not games:
        return False

    starts = start_lines({"games": games})
    if starts:
        faced = sum(g.get("batters_faced") or 0 for g in starts)
        return faced / len(starts) <= OPENER_MAX_BF_PER_START

    share = relief_share({"games": games})
    return share is not None and share >= RELIEF_BF_SHARE_MIN


def opener_pitcher_ids(games: list[dict], season: int | None = None) -> set[int]:
    """Ids of the announced starters on *games* who profile as openers.

    One batched request covering every probable pitcher on the slate, whatever
    the slate size. An unannounced starter contributes no id and so is never
    excluded.
    """
    ids = {
        pid
        for g in games
        for pid in (g.get("home_pitcher_id"), g.get("away_pitcher_id"))
        if pid is not None
    }
    if not ids:
        return set()
    logs = fetch_pitching_game_logs(ids, season or datetime.today().year)
    return {pid for pid in ids if is_opener(logs.get(pid, []))}


def drop_opener_matchups(players: list[dict], opener_ids: set[int]) -> list[dict]:
    """Remove hitters whose opposing starter is an opener.

    **Per side, not per game.** A club using an opener does not stop the other
    club from starting a conventional pitcher, so dropping the whole game would
    discard nine hitters who are facing exactly what this tool is about. The
    filter keys on each player's `opposing_pitcher_id`, which is resolved per
    side in `process_game_lineup`.

    A hitter whose opposing starter is unannounced is **kept**: dropping a
    playable matchup over a missing field is the worse failure, and the field was
    populated on 60 of 60 sides across the two slates probed on 2026-08-09.
    """
    if not opener_ids:
        return players
    return [p for p in players if p.get("opposing_pitcher_id") not in opener_ids]


def fetch_hydrated_schedule(date: str) -> dict[int, dict]:
    """Raw schedule games for *date* keyed by `gamePk`, hydrated with venue and team.

    `fetch_schedule` projects each game down to ids and a start time, which is
    all the ranking run needs. The model needs the venue and the team
    abbreviation as well, and an unhydrated record carries neither: reading one
    costs ten calculator keys per hitter. See issue #43, which tracks folding
    the hydration into `fetch_schedule` itself.

    Returns {} on failure, which resolves the model column to None rather than
    stopping the run.
    """
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "date": date, "hydrate": "team,venue"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Hydrated schedule fetch error ({exc})")
        return {}
    return {
        game["gamePk"]: game
        for entry in resp.json().get("dates", [])
        for game in entry.get("games", [])
    }


def player_model_probability(player: dict, context: dict) -> float | None:
    """Run the full calculator model for one hitter and return P(>=1 hit).

    *context* carries the run-wide inputs: `schedule` (hydrated, by `gamePk`),
    `lineups` (by `gamePk`), `today` and `season`.

    Returns None on any failure rather than raising. The model is an additive
    column on an otherwise working tool, and one hitter whose Statcast pull times
    out must not take the run down with it.
    """
    from calculators.pipeline import assemble, model_probability

    game_pk = player.get("game_pk")
    game = context["schedule"].get(game_pk) or {}
    side = (context["lineups"].get(game_pk) or {}).get(player.get("side")) or []
    try:
        results = assemble(
            player["id"],
            player.get("opposing_pitcher_id"),
            raw_game=game,
            game_pk=game_pk,
            team_id=player.get("team_id"),
            lineup_side=side,
            lineup_spot=player.get("lineup_spot"),
            is_home=player.get("is_home"),
            game_number=player.get("game_number", 1),
            start_time=player.get("start_dt"),
            season=context["season"],
            today=context["today"],
        )
    except Exception as exc:  # noqa: BLE001 - additive column, never fatal
        print(f"  model failed for {player.get('fullName')} ({exc})")
        return None
    return model_probability(results)


def is_in_cooldown(player_id, cache, cooldown_days):
    """True if *player_id* polled with no recent data too recently to be worth rechecking."""
    last_checked = cache.get(str(player_id))
    if not last_checked:
        return False
    last_checked_date = datetime.strptime(last_checked, "%Y-%m-%d")
    return (datetime.now() - last_checked_date) < timedelta(days=cooldown_days)


def is_within_past_week(date_str):
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    one_week_ago = datetime.now() - timedelta(days=7)
    return date_obj >= one_week_ago


def binomial_probability(ab, h, bb):
    if ab == 0:
        return 0.0
    pa = ab + bb
    exp = pa / 5
    avg = h / ab
    return 1 - (1 - avg) ** exp


def scrape_player_data(
    player_id: int,
    player_name: str,
    team_id: int,
    schedule_map: dict | None = None,
) -> dict | None:
    """Fetch last-5-game batting stats for *player_name* from the MLB Stats API."""
    season = datetime.today().year
    team_abbr = TEAM_ID_TO_ABBR.get(team_id, "???")

    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/{player_id}/stats",
            params={
                "stats": "gameLog",
                "group": "hitting",
                "season": season,
                "gameType": "R",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"connection error ({exc})")
        return None

    stats_list = resp.json().get("stats", [])
    if not stats_list:
        return None
    splits = stats_list[0].get("splits", [])
    if not splits:
        return None

    # splits ordered oldest → newest; take the last 5 games
    last5 = splits[-5:]
    last_date_str = last5[-1].get("date", "")
    if not last_date_str or not is_within_past_week(last_date_str):
        return None

    at_bats = sum(g["stat"].get("atBats", 0) for g in last5)
    hits = sum(g["stat"].get("hits", 0) for g in last5)
    walks = sum(g["stat"].get("baseOnBalls", 0) for g in last5)
    strikeouts = sum(g["stat"].get("strikeOuts", 0) for g in last5)

    game_hour = schedule_map.get(team_id) if schedule_map is not None else None
    return {
        "Player": player_name,
        "Team": team_abbr,
        "At Bats": at_bats,
        "Hits": hits,
        "Walks": walks,
        "Strikeouts": strikeouts,
        "GameHourUTC": game_hour,
    }


def refresh_opposing_pitchers(players: list[dict], g: dict) -> list[dict]:
    """Re-resolve each cached player's opposing starter against the game record *g*.

    Cached lineup entries carry `team_id` but not which side they batted on, so
    the opponent is resolved by team instead. A player whose `team_id` matches
    neither side keeps whatever the cache held, rather than being blanked.
    """
    opponent_by_team = {
        g["home_team_id"]: g.get("away_pitcher_id"),
        g["away_team_id"]: g.get("home_pitcher_id"),
    }
    return [
        {
            **p,
            "opposing_pitcher_id": opponent_by_team.get(
                p.get("team_id"), p.get("opposing_pitcher_id")
            ),
        }
        for p in players
    ]


def process_game_lineup(
    g: dict,
    queried_games_cache: dict,
    today: str,
    schedule_map: dict,
    all_players: list,
) -> bool:
    """Fetch and record lineup for *g* unless already queried today.

    Marks the game as queried only when the lineup is posted (non-empty), so
    a pre-lineup cron firing does not block a later firing from picking up the
    posted lineup. An already-queried game still contributes its cached
    players to *all_players* — the cache exists to skip redundant lineup
    fetches within the same day, not to make later same-day runs incomplete.
    Cached players have their opposing starter re-resolved from *g* on every
    run: a lineup can post before the probable pitcher is announced, and the
    cached `None` would otherwise stick for the rest of the day.

    Returns True if a lineup fetch was attempted, False if the game was skipped.
    """
    game_pk = g["gamePk"]
    hour = g["start_dt"].hour
    cached_entry = queried_games_cache.get(str(game_pk))
    if isinstance(cached_entry, dict) and cached_entry.get("date") == today:
        schedule_map[g["home_team_id"]] = hour
        schedule_map[g["away_team_id"]] = hour
        refreshed = refresh_opposing_pitchers(cached_entry.get("players", []), g)
        cached_entry["players"] = refreshed
        all_players.extend(refreshed)
        return False

    lineup = fetch_lineup(game_pk)
    if lineup["home"] or lineup["away"]:
        schedule_map[g["home_team_id"]] = hour
        schedule_map[g["away_team_id"]] = hour
        # Each batter faces the *other* side's probable starter; the flattened
        # list loses which side a batter was on, so resolve it here.
        # `game_pk`, `side`, `is_home`, `game_number` and `start_dt` are carried
        # so the model can rebuild this hitter's full context later without
        # re-reading the schedule. They are additive: nothing in the ranking path
        # reads them, and a cached entry from before they existed still works
        # because every consumer uses `.get`.
        context = {
            "game_pk": game_pk,
            "game_number": g.get("gameNumber", 1),
            "start_dt": g["start_dt"],
        }
        players = [
            {
                **p,
                **context,
                "opposing_pitcher_id": g.get("away_pitcher_id"),
                "side": "home",
                "is_home": True,
            }
            for p in lineup["home"]
        ] + [
            {
                **p,
                **context,
                "opposing_pitcher_id": g.get("home_pitcher_id"),
                "side": "away",
                "is_home": False,
            }
            for p in lineup["away"]
        ]
        all_players.extend(players)
        # Only mark after a posted lineup so a subsequent run can still pull it
        # once it posts.
        queried_games_cache[str(game_pk)] = {"date": today, "players": players}
    return True


def compile_player_data(
    players: list[dict],
    limit: int | None = MAX_PLAYERS,
    cooldown_days=DEFAULT_COOLDOWN_DAYS,
    cache=None,
    schedule_map=None,
    model_context=None,
):
    """Fetch and aggregate batting stats for each player.

    Args:
        players:       List of player dicts with {id, fullName, team_id} from lineup fetch.
        limit:         Maximum number of players to process.  Pass ``None`` to
                       process the entire pool.  Defaults to ``MAX_PLAYERS``.
        cooldown_days: Days to skip a player after they poll with no recent data.
        cache:         {player_id: last_checked_date_str} dict, mutated in place —
                       players are added on a no-data result and cleared on success.
        schedule_map:  {team_id: game_hour_utc} dict derived from fetch_schedule,
                       threaded through to scrape_player_data unchanged.
        model_context: run-wide inputs for the `Model` column, or None to skip
                       it. Computed only for players who survive the cooldown
                       and produce data, because a model evaluation costs a
                       Statcast pull for anyone not already cached today.
    """
    if cache is None:
        cache = {}

    summary_data = []
    player_list = players if limit is None else players[:limit]
    total = len(player_list)

    for i, player in enumerate(player_list, 1):
        name = player["fullName"]
        player_id = player["id"]
        if is_in_cooldown(player_id, cache, cooldown_days):
            continue

        print(f"[{i}/{total}] Fetching {name} ...", end=" ", flush=True)
        player_data = scrape_player_data(
            player_id, name, player["team_id"], schedule_map
        )

        if player_data and player_data["At Bats"] > 0:
            player_data["probability"] = binomial_probability(
                player_data["At Bats"], player_data["Hits"], player_data["Walks"]
            )
            if model_context:
                player_data["model"] = player_model_probability(player, model_context)
            summary_data.append(player_data)
            print(f"ok  ({player_data['Hits']}-{player_data['At Bats']})")
            cache.pop(str(player_id), None)
        else:
            print("skipped (no recent data)")
            cache[str(player_id)] = datetime.now().strftime("%Y-%m-%d")
        sleep(random.uniform(0.6, 1.8))

    return summary_data


def format_model(value: float | None) -> str:
    """Render the model probability, or a dash when it did not resolve.

    A dash rather than a blank or a zero: the model returning nothing is a
    different statement from it returning a low probability, and the table has to
    say which. Absent entirely when the run was not asked for a model.
    """
    return f"{value:.1%}" if value is not None else "-"


def model_delta(data: dict) -> float | None:
    """Model probability minus the heuristic, in probability points, or None.

    **Signed, and the sign is Model minus `Prob %`**, so positive means the model
    likes this hitter more than the five-game heuristic does. The sign carries
    the interesting half of the information: the two methods disagreeing by 20
    points in opposite directions are different findings, and an absolute value
    would collapse them.

    None when the model did not resolve, which is not a zero delta. A hitter the
    model could not evaluate has no disagreement to report, and scoring him as
    perfect agreement would put him at the top of a table sorted by agreement.
    """
    model = data.get("model")
    if model is None or data.get("probability") is None:
        return None
    return model - data["probability"]


def format_delta(value: float | None) -> str:
    """Render a signed delta in **percentage points**, or a dash.

    `Prob %` and `Model` are both printed as percentages, so the difference
    between them reads as points: a .500 heuristic against a .725 model prints
    as `+22.5`, not `+0.2`. The scaling is here rather than in `model_delta`,
    which stays in probability units like everything else in the model.
    """
    return f"{value * 100:+.1f}" if value is not None else "-"


def rank_key(data: dict) -> tuple[float, int, float]:
    """Sort key reconciling the heuristic ranking with the model's disagreement.

    Ascending on this tuple puts **highest probability at the top and lowest at
    the bottom**, which is what the tool ranks on and what the separator between
    best picks and worst performers means. `Prob %` is the primary key and
    nothing about the model displaces it.

    `Delta` is the secondary key, **signed and ascending**, which resolves the two
    endpoints the ordering has to satisfy:

    - The top row is the highest probability and, among hitters the heuristic
      rates equally, the **lowest** delta.
    - The bottom row is the lowest probability and, among equals, the **highest
      positive** delta: the hitter the heuristic likes least and the model
      disagrees with most in his favour.

    Signed, not absolute. An absolute secondary key cannot distinguish those two
    ends, since it collapses a model that disagrees upward with one that
    disagrees downward, and the whole point of the ordering is that they land at
    opposite ends of the table.

    **This is a tie-break, not a re-rank.** Two hitters with different `Prob %`
    are ordered by `Prob %` alone however much the model disagrees. Ties are not
    rare here: `Prob %` is a function of a five-game line, so any two hitters
    sharing hits, at bats and walks collide exactly. Giving the delta weight
    enough to reorder unequal probabilities would need a weighting nobody has
    measured, which is #35.

    A hitter with no model value sorts **after** resolved rows at the same
    probability. He is not a zero delta: nothing is known about the model's
    opinion, so he cannot be placed between a disagreement and an agreement.
    """
    delta = model_delta(data)
    return (
        -data["probability"],
        1 if delta is None else 0,
        0.0 if delta is None else delta,
    )


def probable_hitters(summary_data, n=5):
    """Print the ranked table.

    Highest probability at the top, lowest at the bottom, with the model's
    signed disagreement breaking ties; see `rank_key`. Selection is unchanged:
    the `n * 2` best and `n` worst by `Prob %`, separated by a divider row.
    """
    summary_data.sort(key=rank_key)
    top_players = summary_data[: n * 2]
    low_players = summary_data[-n:]

    table = PrettyTable()
    # Same clock as the slate that was queried, not the host's: a 00:30 UTC run
    # would otherwise title tomorrow's date over tonight's games.
    today = slate_date()
    table.title = f"{today.strftime('%B')} {today.day}, {today.year}"
    # `Model` and `Delta` sit next to `Prob %` deliberately: the two probabilities
    # are different answers to the same question and the point of showing both is
    # the comparison. `Prob %` is the shipped heuristic, a 5-game average put
    # through the binomial with a `pa / 5` exponent. `Model` is the aggregate over
    # every calculator reporting a rate per plate appearance. Neither has been
    # validated against outcomes yet (#35), so the ranking is still `Prob %`.
    table.field_names = ["Player", "Team", "H-AB", "BB/K", "Prob %", "Model", "Delta"]

    def add(data):
        table.add_row(
            [
                data["Player"],
                data["Team"],
                f"{data['Hits']}-{data['At Bats']}",
                f"{data['Walks']}/{data['Strikeouts']}",
                f"{data['probability']:.1%}",
                format_model(data.get("model")),
                format_delta(model_delta(data)),
            ]
        )

    for data in top_players:
        add(data)

    table.add_row(["---"] * len(table.field_names))

    for data in low_players:
        add(data)

    print(table)


def group_picks_by_start_time(
    summary_data: list[dict], top_n: int = 5
) -> dict[int, list[dict]]:
    """Group summary entries by GameHourUTC and return the top *top_n* per group.

    Players with GameHourUTC=None are excluded. Returns a dict keyed by
    GameHourUTC integer, each value sorted by probability descending, capped at *top_n*.
    No minimum floor: a group with fewer than *top_n* players returns all of them.
    """
    groups: dict[int, list[dict]] = {}
    for player in summary_data:
        hour = player.get("GameHourUTC")
        if hour is None:
            continue
        groups.setdefault(hour, []).append(player)
    return {
        hour: sorted(players, key=lambda p: p["probability"], reverse=True)[:top_n]
        for hour, players in groups.items()
    }


def format_sms_body(ranked_list: list[dict], game_hour_utc: int) -> str:
    """Return the SMS text body for one start-time grouping."""
    lines = [f"Top picks — {game_hour_utc:02d}:00 UTC"]
    for i, player in enumerate(ranked_list, 1):
        lines.append(
            f"{i}. {player['Player']} ({player['Team']}) — {player['probability']:.1%}"
        )
    return "\n".join(lines)


def send_sms_notification(
    ranked_list: list[dict],
    game_hour_utc: int,
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
) -> bool:
    """POST an SMS to Twilio's Messages endpoint. Returns True on 2xx, False otherwise."""
    body = format_sms_body(ranked_list, game_hour_utc)
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    try:
        resp = requests.post(
            url,
            auth=(account_sid, auth_token),
            data={"From": from_number, "To": to_number, "Body": body},
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"SMS send failed ({exc})")
        return False
    if not (200 <= resp.status_code < 300):
        print(f"SMS send failed (HTTP {resp.status_code})")
        return False
    return True


def dispatch_scheduled_sms(
    summary: list[dict], sms_sent_cache: dict, today: str
) -> None:
    """Send per-grouping SMS notifications. Only called in --scheduled mode."""
    grouped = group_picks_by_start_time(summary)
    account_sid = env_setting("TWILIO_ACCOUNT_SID")
    auth_token = env_setting("TWILIO_AUTH_TOKEN")
    from_number = env_setting("TWILIO_FROM_NUMBER")
    to_number = env_setting("SUBSCRIBER_PHONE_NUMBER")
    if not all([account_sid, auth_token, from_number, to_number]):
        print(
            "SMS send skipped: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "TWILIO_FROM_NUMBER, or SUBSCRIBER_PHONE_NUMBER not set"
        )
        return
    for game_hour, ranked in grouped.items():
        if is_sms_sent_today(game_hour, sms_sent_cache, today):
            continue
        success = send_sms_notification(
            ranked, game_hour, account_sid, auth_token, from_number, to_number
        )
        if success:
            sms_sent_cache[str(game_hour)] = today


def format_email_body(ranked_list: list[dict], game_hour_utc: int) -> tuple[str, str]:
    """Return the (subject, body) for one start-time grouping's email.

    Email is not length-constrained the way an SMS is, so this carries the same
    columns as the printed table, `BB/K` included, rather than the SMS's name
    and probability alone. Plate discipline is the column that most often
    explains why the two probabilities disagree, so dropping it would leave the
    reader with a delta and no way to interpret it.

    Plain text, no HTML: the recipient is a single known address and a text part
    renders everywhere without a multipart body.
    """
    subject = f"Beat the Streak: top picks for {game_hour_utc:02d}:00 UTC"
    header = f"{'#':<3} {'Player':<22} {'Tm':<4} {'H-AB':<7} {'BB/K':<6} {'Prob %':<8} {'Model':<8} {'Delta':<7}"
    lines = [subject, "", header, "-" * len(header)]
    for i, player in enumerate(ranked_list, 1):
        lines.append(
            f"{i:<3} {player['Player']:<22} {player['Team']:<4} "
            f"{f'{player["Hits"]}-{player["At Bats"]}':<7} "
            f"{f'{player["Walks"]}/{player["Strikeouts"]}':<6} "
            f"{f'{player["probability"]:.1%}':<8} "
            f"{format_model(player.get('model')):<8} "
            f"{format_delta(model_delta(player)):<7}"
        )
    lines += [
        "",
        "Prob %: 5-game heuristic through the binomial.",
        "Model:  aggregate over every calculator reporting hits per plate appearance.",
        "Delta:  Model minus Prob %, in percentage points.",
        "Neither number has been validated against outcomes yet; ranking is Prob %.",
    ]
    return subject, "\n".join(lines)


def send_email_notification(
    ranked_list: list[dict],
    game_hour_utc: int,
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_address: str,
    to_address: str,
) -> bool:
    """Send one grouping's picks over SMTP. Returns True on success.

    Mirrors `send_sms_notification`'s contract exactly, including returning
    False rather than raising, so `dispatch_scheduled_email` can reuse the
    send-cache discipline: a grouping is marked sent only on success, and a
    failure retries on the next tick.

    stdlib `smtplib` and `email.message`, so this adds no dependency, the same
    reasoning that kept the Twilio path on raw `requests`.
    """
    subject, body = format_email_body(ranked_list, game_hour_utc)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = to_address
    message.set_content(body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Email send failed ({exc})")
        return False
    return True


def dispatch_scheduled_email(
    summary: list[dict], email_sent_cache: dict, today: str
) -> None:
    """Send per-grouping email notifications. Only called in --scheduled mode.

    Runs alongside `dispatch_scheduled_sms` rather than replacing it, and keeps
    its own send-cache file. The two channels are independently gated on their
    own credentials, so email works while the Twilio number is unverified and
    neither one's failure suppresses the other.
    """
    grouped = group_picks_by_start_time(summary)
    smtp_host = env_setting("SMTP_HOST")
    smtp_port = env_setting("SMTP_PORT") or "587"
    username = env_setting("SMTP_USERNAME")
    password = env_setting("SMTP_PASSWORD")
    to_address = env_setting("SUBSCRIBER_EMAIL")
    # From defaults to the authenticated user, which is what most providers
    # require anyway; a separate SMTP_FROM only matters for a distinct sender.
    from_address = env_setting("SMTP_FROM") or username
    if not all([smtp_host, username, password, to_address]):
        print(
            "Email send skipped: SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, "
            "or SUBSCRIBER_EMAIL not set"
        )
        return
    try:
        port = int(smtp_port)
    except ValueError:
        print(f"Email send skipped: SMTP_PORT is not a number ({smtp_port!r})")
        return
    for game_hour, ranked in grouped.items():
        if is_sms_sent_today(game_hour, email_sent_cache, today):
            continue
        success = send_email_notification(
            ranked,
            game_hour,
            smtp_host,
            port,
            username,
            password,
            from_address,
            to_address,
        )
        if success:
            email_sent_cache[str(game_hour)] = today


def run(args: argparse.Namespace) -> None:
    """Execute one full run with the given parsed arguments."""
    now = datetime.now(timezone.utc)
    today = slate_date(now).strftime("%Y-%m-%d")
    games = fetch_schedule(today)
    selected = select_games(games, now, scheduled=args.scheduled)

    schedule_map: dict = {}
    all_players: list[dict] = []
    queried_games_cache = load_queried_games_cache()
    for g in selected:
        process_game_lineup(g, queried_games_cache, today, schedule_map, all_players)

    # Filtered here, after the loop, and never inside the queried-games cache.
    # The probable pitcher publishes on a later clock than the lineup and changes
    # on a scratch, so `process_game_lineup` re-resolves it from the fresh
    # schedule record on the cache-hit path. Reading it at this point inherits
    # that: a game dropped at 14:00 for an announced opener comes back at 14:15
    # if he is scratched. Baking the decision into a cache entry would rebuild
    # the exact bug `refresh_opposing_pitchers` exists to prevent.
    if args.exclude_openers:
        openers = opener_pitcher_ids(selected)
        kept = drop_opener_matchups(all_players, openers)
        dropped = len(all_players) - len(kept)
        if dropped:
            print(f"Excluded {dropped} hitters facing {len(openers)} opener(s)")
        all_players = kept

    # The model is opt-out on a manual run and opt-in on a scheduled one, because
    # the two have different deadlines. A hitter whose Statcast frame is already
    # cached for today evaluates in about 2 seconds; one who is not costs a pull
    # of roughly 78 seconds, and a full slate is 50 hitters plus up to 30
    # starters. That is over an hour on the first tick of a day, against a
    # 15-minute cron. Issue #50 tracks making it affordable there.
    want_model = args.model
    if want_model is None:
        want_model = not args.scheduled
    model_context = None
    if want_model:
        model_context = {
            "schedule": fetch_hydrated_schedule(today),
            "lineups": {g["gamePk"]: fetch_lineup(g["gamePk"]) for g in selected},
            "today": now.date(),
            "season": now.year,
        }

    no_data_cache = load_no_data_cache()
    summary = compile_player_data(
        players=all_players,
        limit=MAX_PLAYERS,
        cooldown_days=args.cooldown_days,
        cache=no_data_cache,
        schedule_map=schedule_map,
        model_context=model_context,
    )
    save_no_data_cache(no_data_cache)
    save_queried_games_cache(queried_games_cache)

    probable_hitters(summary, n=5)

    if args.scheduled:
        sms_sent_cache = load_sms_sent_cache()
        dispatch_scheduled_sms(summary, sms_sent_cache, today)
        save_sms_sent_cache(sms_sent_cache)

        email_sent_cache = load_email_sent_cache()
        dispatch_scheduled_email(summary, email_sent_cache, today)
        save_email_sent_cache(email_sent_cache)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Beat the Streak — hit-probability ranking tool"
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help=(
            "Scheduled mode: only process games starting within the next 2 hours. "
            "Without this flag, all games starting at or after now are included (manual mode)."
        ),
    )
    parser.add_argument(
        "--cooldown-days",
        type=int,
        default=DEFAULT_COOLDOWN_DAYS,
        help=f"Days to skip a player after a no-data poll before rechecking (default: {DEFAULT_COOLDOWN_DAYS})",
    )
    parser.add_argument(
        "--exclude-openers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Drop hitters whose opposing starter profiles as an opener rather "
            "than a conventional starting pitcher (default: enabled). This tool "
            "ranks hitters against traditional starters, so an opener's start "
            "is a different matchup than the one being modelled. Filtering is "
            "per side, so the other club's hitters are unaffected. A hitter "
            "whose opposing starter has not been announced is kept. Pass "
            "--no-exclude-openers to rank every posted hitter."
        ),
    )
    parser.add_argument(
        "--model",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Compute the Model column, the aggregate hit probability across "
            "every calculator reporting a rate per plate appearance. Defaults "
            "to enabled on a manual run and disabled with --scheduled, because "
            "a hitter whose Statcast frame is not yet cached for today costs "
            "roughly 78 seconds and a full slate does not fit a 15-minute cron. "
            "The Model column is reported beside Prob %% and does not affect "
            "the ranking, which is still Prob %%."
        ),
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
