"""Category 7 -- Opposing Bullpen Exposure (`CALC_47`-`CALC_51`).

Pure functions over already-normalized bullpen records; the network half lives
in `calculators.sources.category_07_bullpen_exposure`. See `ROADMAP.md` for the
source definition of each `CALC_NN`.

The input is a list of pitcher records, each shaped::

    {"id": int, "full_name": str, "pitch_hand": "L" | "R" | None,
     "games": [<game-log line>, ...]}

where a game-log line carries `game_date`, `game_type`, `game_pk`, `started`,
and the counting fields listed in the source module's `GAME_LOG_FIELDS`.

This category asks about a *group* of pitchers, not one
-------------------------------------------------------
Category 9 evaluates the starter a hitter will actually face. Nobody knows which
relievers a hitter will face, so every calculator here describes the pool: what
it has allowed, how tired it is, which hands it throws with. That makes the
membership rule load-bearing in a way it is not anywhere else in the model, and
`bullpen_pool` is where the whole category's exposure to a bad judgement sits.

Aggregate over appearances, not over pitchers
----------------------------------------------
`CALC_47` and `CALC_50` sum **relief game lines**, so a swingman contributes the
innings he threw out of the bullpen and not the ones he threw as a starter. That
is worth more than it looks: it inverts the cost of a membership mistake.

Aggregating per pitcher would make admitting a starter expensive, because his
two hundred starting batters faced would swamp a real bullpen sample and drag
`CALC_47` toward rotation quality, which is precisely the thing the category
exists to measure separately. Aggregating per appearance makes admitting a
starter nearly free, since a rotation arm has almost no relief lines to
contribute, while dropping a genuine reliever still costs his whole sample. So
the threshold should err **inclusive**, which is the opposite of the instinct.

`CALC_48` and `CALC_49` do count pitchers rather than appearances, since a
fatigue core and a handedness mix are properties of the group's membership. They
are the reason a threshold is needed at all.
"""

from __future__ import annotations

import datetime
from typing import Any, Sequence

from calculators.common import (
    DAYS,
    DELTA,
    MULTIPLIER,
    PROBABILITY,
    Rate,
    SWING_DESCRIPTIONS,
    WHIFF_DESCRIPTIONS,
    apply_window,
    is_competitive,
    rate_or_none,
)

# A pitcher is in the bullpen when at least this share of his competitive
# batters faced came in relief.
#
# Measured across 129 active pitchers on ten clubs, 2026-08-08: 57 sit at
# exactly 1.0 and 40 at exactly 0.0, but the middle is populated rather than
# empty, so this is a real boundary and not a formality. What recommends 0.5 is
# where the straddling cases land. Immediately below it are Miles Mikolas (.497,
# 11 starts and 13 relief outings) and Brayan Bello (.495, 8 and 10), rotation
# arms taking piggyback work. Immediately above are Matt Waldron (.552, 3 and 9)
# and Chad Patrick (.660, 6 and 34), bullpen arms who spot start. The rule sorts
# both pairs the way a human would.
#
# Share of batters faced rather than share of *games*: a pitcher with 8 starts
# and 10 relief outings has a start share of .44 and a relief batters-faced
# share of .50, because one start is worth four relief appearances in workload.
# Games would call him a reliever, which he is not.
RELIEF_BF_SHARE_MIN = 0.5

# Mean batters faced per start at or below which a pitcher profiles as an opener.
#
# Measured over 889 competitive starts by the same 129 pitchers: 786 of them
# (88.4%) ran to 18 or more batters faced, 55 to 12-17, 25 to 7-11 and 23 to 6
# or fewer. Per pitcher, restricted to those with at least three starts, the
# mean-per-start values run 4.7, 5.7, 6.0, 6.5, 9.2 and then jump to 16.0, so the
# boundary is placed in the observed gap rather than at a round number that
# happens to be nearby. A conventional starter's shortest single outing in the
# sample was 13 batters faced, which sits above it.
OPENER_MAX_BF_PER_START = 12.0

# How many arms constitute the leverage core `CALC_48` measures the depletion of.
# Three is the conventional shape of a modern late-inning group (a closer and two
# setup arms) rather than a measured quantity, and it is stated as a convention.
# The alternative, a leverage threshold, needs a league distribution that issue
# #38 blocks.
LEVERAGE_CORE_SIZE = 3

# The ROADMAP is internally inconsistent about `CALC_48`'s window: the title says
# "Recent (7d)" and the body says "last 3 days". Both are emitted rather than one
# being chosen silently, and issue #39 can decide which the composite reads.
FATIGUE_WINDOW_DAYS = 3
FATIGUE_LONG_WINDOW_DAYS = 7

# Days back over which an appearance makes a reliever count as recently used for
# the availability key. Two covers the common "threw on back-to-back days" rule
# of thumb without asserting a usage policy this repo cannot observe.
RECENT_APPEARANCE_DAYS = 2

SWITCH = "S"


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------


def _competitive_before(
    games: Sequence[dict[str, Any]],
    today: datetime.date | None,
) -> list[dict[str, Any]]:
    """Competitive game lines strictly before *today*.

    Today's game has not been played, and a backtest anchored at a past date
    must not see the games that followed it. Non-competitive game types are
    dropped through `common.is_competitive`, which the game log supports for
    free because every split carries `gameType`.
    """
    lines = [g for g in games or [] if is_competitive(g)]
    if today is None:
        return lines
    cutoff = today.isoformat()
    return [g for g in lines if (g.get("game_date") or "") < cutoff]


def relief_lines(
    pitcher: dict[str, Any],
    today: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """The pitcher's relief appearances, competitive and before *today*."""
    return [
        g
        for g in _competitive_before(pitcher.get("games"), today)
        if not g.get("started")
    ]


def start_lines(
    pitcher: dict[str, Any],
    today: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """The pitcher's starts, competitive and before *today*."""
    return [
        g for g in _competitive_before(pitcher.get("games"), today) if g.get("started")
    ]


def relief_share(
    pitcher: dict[str, Any],
    today: datetime.date | None = None,
) -> float | None:
    """Share of the pitcher's batters faced that came in relief, or None.

    None when he has faced nobody, which is a callup with no appearances rather
    than a pitcher with a zero relief share.
    """
    relief = _sum_lines(relief_lines(pitcher, today), "batters_faced")
    started = _sum_lines(start_lines(pitcher, today), "batters_faced")
    total = relief + started
    return relief / total if total else None


def bullpen_pool(
    pitchers: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """The subset of *pitchers* that counts as today's bullpen.

    A pitcher with no appearances at all is **kept**. He is a fresh callup, and a
    callup is in the bullpen far more often than he is in the rotation; more to
    the point, excluding him would drop a genuinely available arm from
    `CALC_49`'s handedness mix on no evidence, while including him costs the
    appearance-weighted calculators nothing because he has no lines to
    contribute. Same asymmetry as the threshold itself.
    """
    pool = []
    for pitcher in pitchers or []:
        share = relief_share(pitcher, today)
        if share is None or share >= RELIEF_BF_SHARE_MIN:
            pool.append(pitcher)
    return pool


def _sum_lines(lines: Sequence[dict[str, Any]], field: str) -> int:
    return sum(line.get(field) or 0 for line in lines)


def _pool_relief_lines(
    bullpen: Sequence[dict[str, Any]],
    today: datetime.date | None,
) -> list[dict[str, Any]]:
    """Every relief line thrown by every pitcher in the pool, flattened."""
    return [line for p in bullpen or [] for line in relief_lines(p, today)]


# ---------------------------------------------------------------------------
# CALC_47 -- bullpen hit allowance
# ---------------------------------------------------------------------------


def calc_47_bullpen_hit_rate(
    bullpen: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_47: what the opposing bullpen has allowed, over relief work only.

    Two keys, and the first is the one that composes.

    - `CALC_47` is hits per **batter faced**. Batters faced is the pitcher-side
      plate-appearance count, so this is on the same scale as `p_hit` and every
      other `PROBABILITY` in the model.
    - `CALC_47_BA` is hits per at bat, which is what "BA allowed" conventionally
      means and what the ROADMAP names. It is emitted because it is the familiar
      number, and it is emitted *second* because it is not the composable one.

    The distinction matters more than it sounds. A walk is a plate appearance
    that is not an at bat, so BA runs above the per-PA rate by roughly the walk
    rate, and blending a BA into a per-PA model is the units error `CALC_57`
    made in the other direction. Both are `PROBABILITY`; only the first should
    reach `CALC_75` without a conversion.
    """
    lines = _pool_relief_lines(bullpen, today)
    hits = _sum_lines(lines, "hits")
    per_pa = rate_or_none(hits, _sum_lines(lines, "batters_faced"))
    per_ab = rate_or_none(hits, _sum_lines(lines, "at_bats"))
    return {
        "CALC_47": PROBABILITY(per_pa) if per_pa is not None else None,
        "CALC_47_BA": PROBABILITY(per_ab) if per_ab is not None else None,
    }


# ---------------------------------------------------------------------------
# CALC_48 -- recent bullpen fatigue
# ---------------------------------------------------------------------------


def leverage_score(
    pitcher: dict[str, Any],
    today: datetime.date | None = None,
) -> float | None:
    """Saves plus holds per relief appearance, or None with no relief work.

    This is the leverage signal the Stats API actually publishes. A save and a
    hold are both recorded only in a close game, so the rate at which a reliever
    collects them is a usable proxy for the innings his manager trusts him with,
    and a mop-up arm collects neither. It is a proxy and not a leverage index:
    `pLI` is not served by this API, and computing one needs the league
    base-out-run-expectancy table that issue #38 blocks.
    """
    lines = relief_lines(pitcher, today)
    if not lines:
        return None
    return (_sum_lines(lines, "saves") + _sum_lines(lines, "holds")) / len(lines)


def leverage_core(
    bullpen: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
    size: int = LEVERAGE_CORE_SIZE,
) -> list[dict[str, Any]]:
    """The *size* highest-leverage arms in the pool, most trusted first.

    Only pitchers who have recorded at least one save or hold qualify, so a
    bullpen with fewer than *size* such arms yields a shorter core rather than
    being padded out with mop-up pitchers, which would report the wrong group as
    depleted. Ties break on player id, so the result is deterministic.
    """
    scored = []
    for pitcher in bullpen or []:
        score = leverage_score(pitcher, today)
        if score:
            scored.append((score, pitcher))
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("id") or 0))
    return [pitcher for _, pitcher in scored[:size]]


def _window_load(
    core: Sequence[dict[str, Any]],
    days: int,
    today: datetime.date | None,
) -> float:
    """Total pitches thrown by *core* in the last *days* days ending yesterday."""
    total = 0
    for pitcher in core:
        # `apply_window` returns None for an empty window, meaning "no evidence".
        # Here an empty window means the opposite: this reliever demonstrably did
        # not pitch, which is a real reading of zero load and the whole point of
        # the calculator. Coercing to [] is deliberate, not defensive.
        recent = (
            apply_window(relief_lines(pitcher, today), DAYS(days), today=today) or []
        )
        total += _sum_lines(recent, "pitches")
    return float(total)


def calc_48_bullpen_fatigue(
    bullpen: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_48: how worked the opposing bullpen's leverage arms are.

    The ROADMAP's mechanism is that a depleted leverage core means the hitter
    sees mop-up pitchers instead, so what is measured is the core's recent load
    rather than the whole bullpen's.

    Three keys.

    - `CALC_48` is pitches thrown per core reliever over the last three days.
    - `CALC_48_7D` is the same over seven days. Both windows exist because the
      ROADMAP names one in the title and the other in the body.
    - `CALC_48_AVAILABLE` is the share of the core that did not appear in the
      last two days.

    The two load keys are `DELTA` in units of **pitches**, not rates, on the
    `CALC_60` Game Score and `CALC_62` raw-mph precedent: no league distribution
    exists to normalize them against while issue #38 is open, and inventing one
    would make every bullpen look like a measured deviation from a real average.
    Their denominators are core size, which is a group size and not a sample
    size, so issue #34 must not read them as evidence weight. `CALC_48_AVAILABLE`
    is a genuine rate in [0, 1] and is `MULTIPLIER`.

    A load of 0.0 is a real reading. All three keys are None only when the core
    is empty, which means no arm in the pool has recorded a save or a hold.

    Two appearances on one date both count, which is what makes this correct on
    a doubleheader: Category 8 keys its game log on `game_pk` to stop a
    doubleheader collapsing into one game, but here the two outings are two
    separate workloads and summing them is the point.
    """
    empty = {"CALC_48": None, "CALC_48_7D": None, "CALC_48_AVAILABLE": None}
    core = leverage_core(bullpen, today)
    if not core:
        return empty

    short = _window_load(core, FATIGUE_WINDOW_DAYS, today)
    long = _window_load(core, FATIGUE_LONG_WINDOW_DAYS, today)

    rested = 0
    for pitcher in core:
        recent = (
            apply_window(
                relief_lines(pitcher, today), DAYS(RECENT_APPEARANCE_DAYS), today=today
            )
            or []
        )
        if not recent:
            rested += 1

    return {
        "CALC_48": DELTA(Rate(short / len(core), len(core))),
        "CALC_48_7D": DELTA(Rate(long / len(core), len(core))),
        "CALC_48_AVAILABLE": MULTIPLIER(Rate(rested / len(core), len(core))),
    }


# ---------------------------------------------------------------------------
# CALC_49 -- projected reliever handedness mix
# ---------------------------------------------------------------------------


def calc_49_handedness_mix(
    bullpen: Sequence[dict[str, Any]],
    batter_side: str | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_49: how much of the bullpen's work comes from the hitter's bad side.

    Weighted by each reliever's relief batters faced rather than counted per
    head, because a bullpen's handedness is only as relevant as the arms that
    actually pitch: a left-hander with four appearances and a left-hander with
    sixty are not the same exposure.

    Two keys.

    - `CALC_49` is the share of relief batters faced thrown by pitchers with the
      platoon advantage over this hitter, which is same-handedness.
    - `CALC_49_LHP` is the share thrown by left-handers, independent of who is
      batting. Emitted because it is the reading that survives not knowing the
      hitter, and because it is what a human means by "how left-handed is this
      bullpen".

    **The ROADMAP scopes this to the sixth through ninth innings and this does
    not.** The game log is one row per appearance and carries no inning, so what
    is measured is the whole relief corps rather than the late-inning group.
    Narrowing it would mean pulling Statcast for every reliever, which is the
    cost `CALC_50_WHIFF` already declines to pay for one key. The two differ by
    the long men, who throw a small share of a bullpen's batters faced and are
    weighted accordingly.

    **A switch hitter is never at a platoon disadvantage**, so `CALC_49` is 0.0
    for him with the full denominator behind it, rather than None. He picks his
    side after the pitcher is announced, so unlike everywhere else in Category 2
    there is no effective side to resolve: against a bullpen he has the edge
    against every arm in it.

    Both are `MULTIPLIER`. A .55 same-handed share is a mix, not a hit
    probability, and blending it directly into `p_hit` would be the role-tag
    error issue #39 is open about.

    Pitchers whose throwing hand did not resolve are excluded from both
    numerator and denominator, so a partial roster gives a rate over what is
    known rather than one diluted toward zero.
    """
    empty = {"CALC_49": None, "CALC_49_LHP": None}

    weights: list[tuple[str, int]] = []
    for pitcher in bullpen or []:
        hand = pitcher.get("pitch_hand")
        if hand not in ("L", "R"):
            continue
        faced = _sum_lines(relief_lines(pitcher, today), "batters_faced")
        if faced:
            weights.append((hand, faced))

    total = sum(faced for _, faced in weights)
    if not total:
        return empty

    left = sum(faced for hand, faced in weights if hand == "L")

    same = None
    if batter_side == SWITCH:
        same = 0.0
    elif batter_side in ("L", "R"):
        same = sum(faced for hand, faced in weights if hand == batter_side) / total

    return {
        "CALC_49": MULTIPLIER(Rate(same, total)) if same is not None else None,
        "CALC_49_LHP": MULTIPLIER(Rate(left / total, total)),
    }


# ---------------------------------------------------------------------------
# CALC_50 -- bullpen contact and whiff
# ---------------------------------------------------------------------------


def calc_50_bullpen_contact(
    bullpen: Sequence[dict[str, Any]],
    today: datetime.date | None = None,
    pitches: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """CALC_50: how often the bullpen lets a ball be put in play, and misses bats.

    Two keys, computed from two different sources at two different scopes, which
    is the thing to know before reading them together.

    - `CALC_50` is balls in play per batter faced, derived from the counting
      line as ``(BF - SO - BB - HBP) / BF``. It is scoped to **relief
      appearances only** and costs nothing beyond the batched game log.
    - `CALC_50_WHIFF` is misses per swing over *pitches*, and is None unless
      pitch-level records are supplied. The Stats API publishes no swing or miss
      counts at any endpoint checked (`pitchArsenal` carries usage and velocity,
      `expectedStatistics` carries outcome estimates, neither carries swings), so
      this needs Statcast, at one network call per reliever. It is optional for
      that reason, on the `CALC_05`-`CALC_08` precedent.

    **The whiff key is not relief-scoped.** The pitch records carry no start
    flag, and the field projection this category requests does not include the
    inning and out state that Category 9's start test would need to reconstruct
    one. Since `bullpen_pool` has already restricted the group to pitchers whose
    majority of work is relief, the contamination is bounded and small, but it is
    real and the two keys are not strictly over the same population.

    Both are `MULTIPLIER`. An in-play rate near .70 and a whiff rate near .25 are
    skill rates, not batting averages.
    """
    lines = _pool_relief_lines(bullpen, today)
    faced = _sum_lines(lines, "batters_faced")
    unbatted = (
        _sum_lines(lines, "strike_outs")
        + _sum_lines(lines, "walks")
        + _sum_lines(lines, "hit_by_pitch")
    )
    in_play = rate_or_none(faced - unbatted, faced)

    swings = [p for p in pitches or [] if p.get("description") in SWING_DESCRIPTIONS]
    whiffs = sum(1 for p in swings if p["description"] in WHIFF_DESCRIPTIONS)
    whiff = rate_or_none(whiffs, len(swings))

    return {
        "CALC_50": MULTIPLIER(in_play) if in_play is not None else None,
        "CALC_50_WHIFF": MULTIPLIER(whiff) if whiff is not None else None,
    }


# ---------------------------------------------------------------------------
# CALC_51 -- opener and bulk reliever
# ---------------------------------------------------------------------------


def calc_51_opener_adjustment(
    starter: dict[str, Any] | None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """CALC_51: whether today's announced starter profiles as an opener.

    Two keys.

    - `CALC_51` is a 0/1 indicator: he has started before, and his starts have
      averaged no more than `OPENER_MAX_BF_PER_START` batters faced.
    - `CALC_51_BF_PER_START` is that mean, in batters faced.

    Both are `DELTA` and neither denominator is a sample size; the indicator's
    is 1 and the mean's is his start count. The ROADMAP frames the consequence
    as a matchup shift on the hitter's second and third plate appearances,
    which is a `CALC_44` interaction rather than a number this module can put a
    value on. Issue #39 owns what the condition is worth.

    **This is a prediction from history, not an observation.** Nothing published
    before first pitch says "opener"; what is available is that this pitcher's
    previous starts were short. A first-time opener therefore reads 0.0, and a
    pitcher returning from injury on a strict pitch limit reads 1.0 without being
    an opener at all. Category 9 makes the same trade in the other direction and
    the framing is deliberate: report the measurable thing under a name that
    says what was measured.

    Returns None for both when he has never started, which is the correct answer
    for a pitcher making his first career start rather than a claim that he is
    not an opener.
    """
    empty = {"CALC_51": None, "CALC_51_BF_PER_START": None}
    if not starter:
        return empty

    starts = start_lines(starter, today)
    if not starts:
        return empty

    per_start = _sum_lines(starts, "batters_faced") / len(starts)
    return {
        "CALC_51": DELTA(Rate(1.0 if per_start <= OPENER_MAX_BF_PER_START else 0.0, 1)),
        "CALC_51_BF_PER_START": DELTA(Rate(per_start, len(starts))),
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def compute_category_07(
    bullpen: Sequence[dict[str, Any]] | None = None,
    batter_side: str | None = None,
    starter: dict[str, Any] | None = None,
    today: datetime.date | None = None,
    reliever_pitches: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run every Category 7 calculator.

    Absent input resolves every key to ``None`` rather than omitting it, matching
    the contract of `compute_category_01` through `compute_category_10`.

    *bullpen* is the opposing club's active pitchers with game logs attached,
    from `calculators.sources.category_07_bullpen_exposure.fetch_bullpen`. The
    pool is narrowed here rather than by the caller, so a caller cannot
    accidentally pass an unfiltered roster to some calculators and a filtered one
    to others.

    *starter* is today's announced starting pitcher in the same record shape. He
    is passed separately because `CALC_51` is about him specifically and he is
    normally not in the bullpen pool. **He arrives from
    `hydrate=probablePitcher`, which publishes on a later clock than the lineup
    and changes on a scratch**, so anything wiring this in needs the cache-hit
    re-resolution `refresh_opposing_pitchers` does; issue #44 tracks it.

    *today* anchors every recency window and is threaded through rather than read
    from the clock, so issue #35 can evaluate this category against a past date.

    *reliever_pitches* is optional Statcast data for `CALC_50_WHIFF` only; see
    that calculator for the cost.
    """
    pool = bullpen_pool(bullpen or [], today)

    results: dict[str, Any] = {}
    results.update(calc_47_bullpen_hit_rate(pool, today))
    results.update(calc_48_bullpen_fatigue(pool, today))
    results.update(calc_49_handedness_mix(pool, batter_side, today))
    results.update(calc_50_bullpen_contact(pool, today, reliever_pitches))
    results.update(calc_51_opener_adjustment(starter, today))
    return results
