import argparse
import json
import os
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from prettytable import PrettyTable
from time import sleep
import random

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

# Where /schedule request failures are logged for later review.
SCHEDULE_ERROR_LOG_FILE = Path(__file__).parent / ".cache" / "schedule_fetch_errors.log"

# Days a player stays skipped after polling with no recent data — a fresh
# game log won't have accumulated in less time than this. Overridable per
# run via `--cooldown-days`.
DEFAULT_COOLDOWN_DAYS = 7


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


def load_queried_games_cache(path=QUERIED_GAMES_CACHE_FILE):
    """Return the {gamePk_str: {"date": ..., "players": [...]}} cache from a prior run, or {} if absent/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_queried_games_cache(cache, path=QUERIED_GAMES_CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


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


def is_sms_sent_today(game_hour_utc: int, cache: dict, today: str) -> bool:
    """True if an SMS for *game_hour_utc* was already sent on *today*."""
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
    home_pitcher_id, away_pitcher_id}. Both games of a doubleheader appear as
    distinct entries. Probable pitcher ids are None until the team announces a
    starter, and stay None for an opener the API does not list.
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
                "home_pitcher_id": probable_pitcher_id(game, "home"),
                "away_pitcher_id": probable_pitcher_id(game, "away"),
            }
        )
    return schedule


def fetch_lineup(game_pk: int) -> dict[str, list[dict]]:
    """Return posted batting-order players for *game_pk* keyed by "home" and "away".

    Each player dict: {id, fullName, team_id}. A team with an empty or absent
    battingOrder returns an empty list for that side. Returns {"home": [], "away": []}
    on request failure without raising.
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
                }
            )
        result[side] = side_list
    return result


def select_games(
    games: list[dict], now: datetime, scheduled: bool = False
) -> list[dict]:
    """Filter *games* to those relevant for this run.

    Manual mode (scheduled=False): games with start_dt >= now.
    Scheduled mode (scheduled=True): games where 0 < start_dt - now <= 2 hours.
    """
    if scheduled:
        window = timedelta(hours=2)
        return [g for g in games if timedelta(0) < g["start_dt"] - now <= window]
    return [g for g in games if g["start_dt"] >= now]


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
        players = [
            {**p, "opposing_pitcher_id": g.get("away_pitcher_id")}
            for p in lineup["home"]
        ] + [
            {**p, "opposing_pitcher_id": g.get("home_pitcher_id")}
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
            summary_data.append(player_data)
            print(f"ok  ({player_data['Hits']}-{player_data['At Bats']})")
            cache.pop(str(player_id), None)
        else:
            print("skipped (no recent data)")
            cache[str(player_id)] = datetime.now().strftime("%Y-%m-%d")
        sleep(random.uniform(0.6, 1.8))

    return summary_data


def probable_hitters(summary_data, n=5):
    # Sort summary data based on descending probability
    summary_data.sort(key=lambda x: x["probability"], reverse=True)

    # n highest probability players
    top_players = summary_data[: n * 2]

    # n lowest probability players
    low_players = summary_data[-n:]

    # Create and populate the table
    table = PrettyTable()
    today = datetime.today()
    table.title = f"{today.strftime('%B')} {today.day}, {today.year}"
    table.field_names = ["Player", "Team", "H-AB", "BB/K", "Prob %"]

    for data in top_players:
        probability = f"{data['probability']:.1%}"
        table.add_row(
            [
                data["Player"],
                data["Team"],
                f"{data['Hits']}-{data['At Bats']}",
                f"{data['Walks']}/{data['Strikeouts']}",
                probability,
            ]
        )

    # Separator row
    table.add_row(["---"] * len(table.field_names))

    for data in low_players:
        probability = f"{data['probability']:.1%}"
        table.add_row(
            [
                data["Player"],
                data["Team"],
                f"{data['Hits']}-{data['At Bats']}",
                f"{data['Walks']}/{data['Strikeouts']}",
                probability,
            ]
        )

    # Display the output
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
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    to_number = os.environ.get("SUBSCRIBER_PHONE_NUMBER", "")
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


def run(args: argparse.Namespace) -> None:
    """Execute one full run with the given parsed arguments."""
    today = datetime.today().strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)
    games = fetch_schedule(today)
    selected = select_games(games, now, scheduled=args.scheduled)

    schedule_map: dict = {}
    all_players: list[dict] = []
    queried_games_cache = load_queried_games_cache()
    for g in selected:
        process_game_lineup(g, queried_games_cache, today, schedule_map, all_players)

    no_data_cache = load_no_data_cache()
    summary = compile_player_data(
        players=all_players,
        limit=MAX_PLAYERS,
        cooldown_days=args.cooldown_days,
        cache=no_data_cache,
        schedule_map=schedule_map,
    )
    save_no_data_cache(no_data_cache)
    save_queried_games_cache(queried_games_cache)

    probable_hitters(summary, n=5)

    if args.scheduled:
        sms_sent_cache = load_sms_sent_cache()
        dispatch_scheduled_sms(summary, sms_sent_cache, today)
        save_sms_sent_cache(sms_sent_cache)


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
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
