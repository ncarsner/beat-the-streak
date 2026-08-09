"""Category 9: Pitcher Trending Form & Fatigue (`CALC_60`-`CALC_65`).

Pure functions over already-normalized pitch records; the network half lives in
`calculators.sources.category_09_pitcher_form`. See `ROADMAP.md` for the source
definition of each `CALC_NN`.

The pitcher-side mirror of Category 8, and single-sided for the same reason:
nothing about the opposing hitter enters. It inherits Category 8's two structural
decisions, the spring-training filter and the `today` parameter, and adds one of
its own.

**Windows are counted in starts, not days or games.** The ROADMAP asks for "last
2 starts" and "last 3 starts", and a starter's calendar is regular enough that
those are the natural units. Nothing here routes through `apply_window`, which
has no start-shaped window and could not easily gain one: identifying a start is
a reconstruction, not a filter.

**A start has to be reconstructed, because Statcast publishes no starter flag.**
The test used here is that the pitcher's earliest plate appearance in the game
came in inning 1 with zero outs recorded. That held for 19 of 19 games in the
probe frame. It classifies an opener as a start, which is the honest reading: an
opener does start. It would misclassify a reliever who entered to begin the first
inning, which requires the starter to face nobody at all.

**Innings pitched has to be reconstructed too, and the rule is not the obvious
one.** Counting outs from plate-appearance `events` undercounts, because a
baserunner retired on a batted ball produces an out the batter's event does not
name: 2 of 104 probe half-innings came up one out short that way. The rule used
instead leans on game state. Within a start, every half-inning the pitcher
appears in *except his last* must have ended with him on the mound, so it
contributed exactly ``3 - outs_when_up`` outs. Only the final half-inning is
ambiguous, and it falls back to the event map. Innings pitched is therefore
**exact except possibly in the final inning of a start, where it is a lower
bound**.

**Nothing here is wired into a run.** The calculators are built and tested in
isolation and get consumed compositely in a later release; the ranked table is
untouched and no Category 9 request is made during a daily run.
"""

from __future__ import annotations

import datetime
from typing import Any, Sequence

from calculators.common import (
    DELTA,
    HARD_HIT_MPH,
    HIT_EVENTS,
    IN_PLAY,
    MULTIPLIER,
    PITCH_CLASS_FASTBALL,
    Rate,
    is_competitive,
    is_in_zone,
    pitch_class,
    rate_or_none,
    terminal_pitch_by_pa,
    zone_code,
)

# Outs recorded by a plate appearance's terminal event, for the events that
# retire more than one runner. Anything else in `common.OUT_EVENTS` is one out
# and anything outside it is none.
#
# This map is the *fallback* path only, used for the final half-inning of a start
# where game state cannot settle the count. It is known to undercount: a
# baserunner retired on a batted ball is an out that the batter's event does not
# name, which cost 1 out in 2 of the probe frame's 104 half-innings.
MULTI_OUT_EVENTS = {
    "grounded_into_double_play": 2,
    "double_play": 2,
    "strikeout_double_play": 2,
    "sac_fly_double_play": 2,
    "sac_bunt_double_play": 2,
    "triple_play": 3,
}

SINGLE_OUT_EVENTS = frozenset(
    {
        "field_out",
        "strikeout",
        "force_out",
        "sac_fly",
        "sac_bunt",
        "fielders_choice_out",
        "other_out",
        "batter_interference",
    }
)

# Statcast's zone code for the middle third of the plate, vertically and
# horizontally: the "meatball" a hitter does the most damage on. `CALC_63` tracks
# how often a pitcher leaves one there.
MEATBALL_ZONE = 5

# Rest-day tiers, in days between starts, taken verbatim from the ROADMAP
# ("3d short, 4-5d normal, 6+d long rest split").
REST_TIER_SHORT = "short"
REST_TIER_NORMAL = "normal"
REST_TIER_LONG = "long"
REST_TIER_BOUNDS_DAYS = (4, 6)

# Bill James's original Game Score needs an earned/unearned run split, and
# Statcast publishes neither: `bat_score` is the batting team's total, with no
# scoring decision attached. Tom Tango's Game Score v2 is used instead because
# every one of its terms is reachable from pitch-level data. The formula:
#
#   40 + 2*(outs) + (strikeouts) - 2*(walks) - 2*(hits) - 3*(runs) - 6*(home runs)
#
# Calibration is left unclaimed. v2 is designed to sit on roughly the same scale
# as v1, but this repo has no league sample to check that against: the bulk
# `pybaseball.statcast()` pull is broken at the pinned version (issue #38), so
# only per-player frames are reachable.
GAME_SCORE_V2_BASE = 40


# ---------------------------------------------------------------------------
# Start reconstruction
# ---------------------------------------------------------------------------


def _outs_for_event(event: str | None) -> int:
    """Outs recorded by a plate appearance's terminal *event*."""
    if event in MULTI_OUT_EVENTS:
        return MULTI_OUT_EVENTS[event]
    return 1 if event in SINGLE_OUT_EVENTS else 0


def _half_inning_outs(half: Sequence[dict[str, Any]], is_final: bool) -> int:
    """Outs the pitcher recorded in one half-inning.

    *half* is his plate appearances in that half-inning, **already sorted by
    ``at_bat_number``**; only the first and last elements are read, so an unsorted
    argument returns a wrong number rather than raising. `_game_outs` is the only
    caller and it sorts. *is_final* marks the last half-inning of the outing.

    For every half-inning but the last, the pitcher was still on the mound when
    it ended, so he recorded exactly the outs remaining when he entered it. That
    is game state rather than a reconstruction, and it is why this path does not
    inherit the event map's undercount.
    """
    entry_outs = half[0].get("outs_when_up") or 0
    if not is_final:
        return 3 - int(entry_outs)
    last = half[-1]
    exit_outs = int(last.get("outs_when_up") or 0) + _outs_for_event(last.get("events"))
    return max(0, exit_outs - int(entry_outs))


def _game_outs(pas: Sequence[dict[str, Any]]) -> int:
    """Outs recorded across a whole start.

    Half-innings are ordered by ``inning`` alone, deliberately. Ordering on the
    ``(inning, inning_topbot)`` tuple would work only by the accident that
    ``"Bot" < "Top"`` alphabetically; a single pitcher throws one side of an
    inning, so the second term never discriminates and including it would make
    the ordering depend on a string comparison that means nothing here.
    """
    innings = sorted({pa.get("inning") for pa in pas if pa.get("inning") is not None})
    total = 0
    for index, inning in enumerate(innings):
        half = sorted(
            (pa for pa in pas if pa.get("inning") == inning),
            key=lambda pa: pa.get("at_bat_number") or 0,
        )
        total += _half_inning_outs(half, is_final=index == len(innings) - 1)
    return total


def _game_runs(pitches: Sequence[dict[str, Any]]) -> int:
    """Runs that scored while the pitcher was on the mound.

    Measured per half-inning as the batting team's score at the end minus its
    score at the start, over **all** his pitch rows rather than only the terminal
    pitch of each plate appearance. Runs can score on a non-terminal pitch (a wild
    pitch, a passed ball, a balk, a steal of home), and summing terminal-row
    deltas missed one such run in 1 of the probe frame's 19 starts.

    **Known undercount, and it is not random.** A run charged to this pitcher that
    scores after he leaves, driven home by a reliever, never appears in his rows
    at all. That biases `CALC_60` *upward* precisely on the starts where he was
    pulled with runners aboard, which is to say precisely on his worst ones. There
    is no signal in the pitch feed that would fix it.

    Grouped on ``(game_pk, inning)`` rather than on ``inning`` alone, even though
    the only caller passes one game's rows. Scoping to the inning by itself would
    make a whole-season argument collapse every game's first inning into one
    group and return ``max(post_bat_score) - min(bat_score)`` across the year: a
    large, plausible, entirely wrong number. Correct regardless of caller costs
    one tuple.
    """
    runs = 0
    halves: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for pitch in pitches:
        inning = pitch.get("inning")
        if inning is not None:
            halves.setdefault((pitch.get("game_pk"), inning), []).append(pitch)
    for rows in halves.values():
        opened = [p["bat_score"] for p in rows if p.get("bat_score") is not None]
        closed = [
            p["post_bat_score"] for p in rows if p.get("post_bat_score") is not None
        ]
        if opened and closed:
            runs += max(0, int(max(closed)) - int(min(opened)))
    return runs


def _is_start(pas: Sequence[dict[str, Any]]) -> bool:
    """Whether these plate appearances are a starting appearance.

    True when the earliest plate appearance came in inning 1 with zero outs
    recorded. Statcast publishes no starter flag, so this is a reconstruction; see
    the module docstring for what it does and does not catch.
    """
    if not pas:
        return False
    first = min(pas, key=lambda pa: pa.get("at_bat_number") or 0)
    return first.get("inning") == 1 and (first.get("outs_when_up") or 0) == 0


def starts(pitches: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce *pitches* to one record per competitive start, oldest first.

    Each record carries the counting line Game Score and the rate calculators
    need, plus ``pitches``, the start's raw rows, which `CALC_62`, `CALC_63`, and
    `CALC_65` read for velocity, location, and contact quality.

    Relief appearances are dropped entirely. Every Category 9 calculator is about
    the starter a hitter is scheduled to face, and a reliever's line answers a
    different question.
    """
    competitive = [p for p in pitches if is_competitive(p)]
    by_game: dict[Any, list[dict[str, Any]]] = {}
    for pitch in competitive:
        game = pitch.get("game_pk")
        if game is not None:
            by_game.setdefault(game, []).append(pitch)

    records = []
    for game, game_pitches in by_game.items():
        pas = terminal_pitch_by_pa(game_pitches)
        if not _is_start(pas):
            continue
        events = [pa.get("events") for pa in pas]
        records.append(
            {
                "game_pk": game,
                "game_date": game_pitches[0].get("game_date"),
                "outs": _game_outs(pas),
                "runs": _game_runs(game_pitches),
                "hits": sum(1 for e in events if e in HIT_EVENTS),
                "home_runs": sum(1 for e in events if e == "home_run"),
                "walks": sum(1 for e in events if e in ("walk", "intent_walk")),
                "strikeouts": sum(1 for e in events if e == "strikeout"),
                "batters_faced": len(pas),
                "pitches": game_pitches,
                "plate_appearances": pas,
            }
        )
    return sorted(records, key=lambda r: (r["game_date"] or "", r["game_pk"]))


def game_score_v2(start: dict[str, Any]) -> int:
    """Tom Tango's Game Score v2 for one start.

    Bill James's original is unreachable: it splits earned from unearned runs and
    Statcast attaches no scoring decision to a run. See `GAME_SCORE_V2_BASE`.
    """
    return (
        GAME_SCORE_V2_BASE
        + 2 * start["outs"]
        + start["strikeouts"]
        - 2 * start["walks"]
        - 2 * start["hits"]
        - 3 * start["runs"]
        - 6 * start["home_runs"]
    )


def _pitches_over(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every pitch row across *records*."""
    return [p for record in records for p in record["pitches"]]


def _fastball_velocity(pitches: Sequence[dict[str, Any]]) -> float | None:
    """Mean release speed of the fastballs among *pitches*.

    Fastballs only, matching `CALC_20`'s reasoning: a mean over the whole arsenal
    tracks pitch selection rather than arm strength, so a start with more breaking
    balls would read as lost velocity.
    """
    speeds = [
        float(p["release_speed"])
        for p in pitches
        if pitch_class(p.get("pitch_type")) == PITCH_CLASS_FASTBALL
        and p.get("release_speed") is not None
    ]
    return sum(speeds) / len(speeds) if speeds else None


def _zone_rate(pitches: Sequence[dict[str, Any]]) -> float | None:
    """Share of tracked pitches located in the strike zone."""
    tracked = [p for p in pitches if zone_code(p) is not None]
    if not tracked:
        return None
    return sum(1 for p in tracked if is_in_zone(p)) / len(tracked)


def _walk_rate(records: Sequence[dict[str, Any]]) -> float | None:
    """Walks per batter faced across *records*."""
    faced = sum(r["batters_faced"] for r in records)
    if not faced:
        return None
    return sum(r["walks"] for r in records) / faced


def _multiplier(rate: Rate | None) -> MULTIPLIER | None:
    return MULTIPLIER(rate) if rate is not None else None


# ---------------------------------------------------------------------------
# CALC_60 -- recent Game Score
# ---------------------------------------------------------------------------


def calc_60_recent_game_score(
    pitches: Sequence[dict[str, Any]],
    starts_back: int = 2,
    today: datetime.date | None = None,
) -> DELTA | None:
    """CALC_60: mean Game Score v2 over the starter's last *starts_back* starts.

    Units are **Game Score points**, not a rate, so the role is `DELTA` on the
    `CALC_19` precedent. Higher is a better start. The `Rate`'s denominator counts
    the starts averaged, so two starts on record is distinguishable from two out
    of thirty.

    *today* is accepted and unused; the window is start-anchored.
    """
    recent = starts(pitches)[-starts_back:]
    if not recent:
        return None
    scores = [game_score_v2(s) for s in recent]
    return DELTA(Rate(sum(scores) / len(scores), len(recent)))


# ---------------------------------------------------------------------------
# CALC_61 -- recent hit allowance
# ---------------------------------------------------------------------------


def calc_61_recent_hit_allowance(
    pitches: Sequence[dict[str, Any]],
    starts_back: int = 3,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_61: hits per nine innings and WHIP over the last *starts_back* starts.

    Two keys, since the ROADMAP names both and neither is derivable from the
    other: WHIP folds in walks, hits per nine does not.

    Both denominators are innings pitched, reconstructed from game state rather
    than counted from events. See the module docstring: innings pitched is exact
    except possibly in the final inning of a start, where it is a lower bound.
    A lower bound on the denominator makes both of these an *upper* bound, which
    is the safer direction for a statistic that flags a struggling starter.

    Both are `MULTIPLIER`, not `PROBABILITY`. A WHIP of 1.2 and a hits-per-nine of
    8.5 are neither probabilities nor on the `p_hit` scale.
    """
    recent = starts(pitches)[-starts_back:]
    outs = sum(s["outs"] for s in recent)
    if not recent or not outs:
        return {"CALC_61_HITS_PER_9": None, "CALC_61_WHIP": None}

    innings = outs / 3.0
    hits = sum(s["hits"] for s in recent)
    walks = sum(s["walks"] for s in recent)
    return {
        "CALC_61_HITS_PER_9": _multiplier(Rate(9.0 * hits / innings, outs)),
        "CALC_61_WHIP": _multiplier(Rate((hits + walks) / innings, outs)),
    }


# ---------------------------------------------------------------------------
# CALC_62 -- velocity delta
# ---------------------------------------------------------------------------


def calc_62_velocity_delta(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> DELTA | None:
    """CALC_62: last start's fastball velocity minus the season average, in mph.

    Signed `DELTA` in mph. **Negative is the interesting direction**: the ROADMAP
    calls out a loss of 1.5 mph or more as a hit boost, so a consumer reading the
    magnitude without the sign would treat a velocity spike as a red flag.

    The season baseline includes the last start. Excluding it would compare the
    start against a baseline that shifts with every start's own length, which
    makes the delta depend on how deep he went rather than on how hard he threw.
    The self-inclusion shrinks the delta slightly and does so consistently.

    Returns None when either side has no fastball, which is the honest answer for
    a pitcher who threw none rather than a zero-mph difference.
    """
    record = starts(pitches)
    if not record:
        return None
    last = _fastball_velocity(record[-1]["pitches"])
    season = _fastball_velocity(_pitches_over(record))
    if last is None or season is None:
        return None
    return DELTA(Rate(last - season, len(record)))


# ---------------------------------------------------------------------------
# CALC_63 -- command and walk delta
# ---------------------------------------------------------------------------


def calc_63_command_delta(
    pitches: Sequence[dict[str, Any]],
    starts_back: int = 3,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_63: recent command against the starter's own season baseline.

    Three keys, because the ROADMAP's "recent Zone % drop -> middle-middle mistake
    pitch rate" bundles three distinct quantities.

    - `CALC_63` is the **primary**: walk rate recent minus season, per batter
      faced. Signed `DELTA`; positive means he is walking more than usual.
    - `CALC_63_ZONE_DELTA` is zone rate recent minus season. Signed `DELTA`;
      negative is the "zone percent drop" the ROADMAP names.
    - `CALC_63_MEATBALL` is the share of his recent tracked pitches left in zone
      5, the middle third both ways. `MULTIPLIER`, a rate in [0, 1].

    Both deltas compare **the same start-based windows**: last *starts_back*
    starts against the whole season, on both legs. Mixing a start window on one
    leg with a calendar window on the other would make the difference partly an
    artifact of which games fell where.
    """
    record = starts(pitches)
    recent = record[-starts_back:]
    if not record or not recent:
        return {"CALC_63": None, "CALC_63_ZONE_DELTA": None, "CALC_63_MEATBALL": None}

    recent_pitches = _pitches_over(recent)
    season_pitches = _pitches_over(record)

    walk_recent, walk_season = _walk_rate(recent), _walk_rate(record)
    walk_delta = (
        DELTA(Rate(walk_recent - walk_season, sum(r["batters_faced"] for r in recent)))
        if walk_recent is not None and walk_season is not None
        else None
    )

    zone_recent, zone_season = _zone_rate(recent_pitches), _zone_rate(season_pitches)
    tracked_recent = sum(1 for p in recent_pitches if zone_code(p) is not None)
    zone_delta = (
        DELTA(Rate(zone_recent - zone_season, tracked_recent))
        if zone_recent is not None and zone_season is not None
        else None
    )

    meatball = (
        rate_or_none(
            sum(1 for p in recent_pitches if zone_code(p) == MEATBALL_ZONE),
            tracked_recent,
        )
        if tracked_recent
        else None
    )

    return {
        "CALC_63": walk_delta,
        "CALC_63_ZONE_DELTA": zone_delta,
        "CALC_63_MEATBALL": _multiplier(meatball),
    }


# ---------------------------------------------------------------------------
# CALC_64 -- rest days
# ---------------------------------------------------------------------------


def rest_tier(days: int | None) -> str | None:
    """Bucket days of rest into the ROADMAP's short / normal / long split."""
    if days is None:
        return None
    short_bound, long_bound = REST_TIER_BOUNDS_DAYS
    if days < short_bound:
        return REST_TIER_SHORT
    if days < long_bound:
        return REST_TIER_NORMAL
    return REST_TIER_LONG


def calc_64_rest_days(
    pitches: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> DELTA | None:
    """CALC_64: days between the starter's last start and *today*.

    Units are **days**, signed `DELTA` on the `CALC_56` precedent. This is the one
    calculator in the category that genuinely needs `today`, since the start being
    rested for has not happened yet and is therefore not in the data.

    **The denominator is not a sample size here, and no honest choice makes it
    one.** Rest is a single scalar read off one date; there is nothing to average
    and nothing to shrink toward a prior. It is set to 1 to satisfy the `Rate`
    shape, and a consumer must not read it as evidence weight the way it reads
    `CALC_60`'s start count. The tier the ROADMAP asks for is available from
    `rest_tier`, kept as a separate function because a tier is a label and the
    role contract carries numbers.

    Defaults to the real current date when *today* is omitted, matching
    `apply_window`.
    """
    record = starts(pitches)
    if not record:
        return None
    last_date = record[-1].get("game_date")
    if not last_date:
        return None
    try:
        previous = datetime.date.fromisoformat(str(last_date)[:10])
    except ValueError:
        return None
    if today is None:
        today = datetime.date.today()
    return DELTA(Rate(float((today - previous).days), 1))


# ---------------------------------------------------------------------------
# CALC_65 -- recent hard contact allowed
# ---------------------------------------------------------------------------


def calc_65_recent_hard_hit_allowed(
    pitches: Sequence[dict[str, Any]],
    starts_back: int = 2,
    today: datetime.date | None = None,
) -> MULTIPLIER | None:
    """CALC_65: share of batted balls hit hard against him in his last starts.

    Hard is at or above `common.HARD_HIT_MPH`, the same threshold `CALC_05` and
    `CALC_55_HARD_HIT` use. The denominator is batted balls carrying an exit
    velocity, not plate appearances and not all batted balls: a null reading is no
    measurement rather than a soft-hit ball.

    `MULTIPLIER`, since a hard-hit rate near .40 is not on the `p_hit` scale.
    """
    recent = starts(pitches)[-starts_back:]
    if not recent:
        return None
    batted = [
        pa
        for record in recent
        for pa in record["plate_appearances"]
        if pa.get("description") == IN_PLAY and pa.get("launch_speed") is not None
    ]
    return _multiplier(
        rate_or_none(
            sum(1 for pa in batted if float(pa["launch_speed"]) >= HARD_HIT_MPH),
            len(batted),
        )
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_09(
    pitches: Sequence[dict[str, Any]] | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Run every Category 9 calculator.

    Absent input resolves every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_08` so a
    consumer can rely on the key set without knowing what was fetched.

    *pitches* is every pitch the starter threw this season, from
    `calculators.sources.category_09_pitcher_form.fetch_pitcher_form`.
    """
    pitches = pitches or []
    results: dict[str, Any] = {
        "CALC_60": calc_60_recent_game_score(pitches, today=today),
        "CALC_62": calc_62_velocity_delta(pitches, today=today),
        "CALC_64": calc_64_rest_days(pitches, today=today),
        "CALC_65": calc_65_recent_hard_hit_allowed(pitches, today=today),
    }
    for calculator in (calc_61_recent_hit_allowance, calc_63_command_delta):
        results.update(calculator(pitches, today=today))
    return results
