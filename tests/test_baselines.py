"""Tests for the league-baseline loader and the generator's aggregation.

The generator's network half is never exercised here — only its pure
aggregation, which is why that function was split out of main().
"""

import json

import pytest

from calculators.baselines import (
    BALLPARKS_PATH,
    PARK_FACTORS_PATH,
    PLATOON_CELLS,
    load_ballparks,
    load_league_game_context,
    load_league_platoon_baseline,
    load_park_factors,
)
from scripts.generate_league_platoon_baseline import aggregate_platoon_baseline
from scripts.generate_league_game_context import skipped_ninth_rate
from scripts.generate_park_factors import parse_leaderboard


# ---------------------------------------------------------------------------
# aggregate_platoon_baseline
# ---------------------------------------------------------------------------


def test_aggregate_builds_all_four_cells():
    """Two batter hands x two split codes produce four distinct cells."""
    rows = [
        (1, "vl", 100, 20, 110),
        (1, "vr", 200, 60, 220),
        (2, "vl", 100, 30, 110),
        (2, "vr", 200, 40, 220),
    ]
    result = aggregate_platoon_baseline(rows, {1: "L", 2: "R"})

    assert set(result) == set(PLATOON_CELLS)
    assert result["L_vs_L"]["rate"] == pytest.approx(0.20)
    assert result["L_vs_R"]["rate"] == pytest.approx(0.30)
    assert result["R_vs_L"]["rate"] == pytest.approx(0.30)
    assert result["R_vs_R"]["rate"] == pytest.approx(0.20)


def test_aggregate_sums_multiple_players_into_one_cell():
    """Every player of a given hand contributes to the same cell."""
    rows = [(1, "vl", 100, 25, 110), (2, "vl", 300, 75, 330)]
    cell = aggregate_platoon_baseline(rows, {1: "L", 2: "L"})["L_vs_L"]

    assert cell["denominator"] == 400
    assert cell["rate"] == pytest.approx(0.25)
    assert cell["plate_appearances"] == 440


def test_aggregate_excludes_switch_hitters():
    """A switch hitter has no fixed batter hand, so belongs to no cell.

    Folding them into one would corrupt the very baseline CALC_13 exists to
    measure them against.
    """
    rows = [(1, "vl", 100, 20, 110), (2, "vl", 100, 90, 110)]
    result = aggregate_platoon_baseline(rows, {1: "L", 2: "S"})

    assert result["L_vs_L"]["denominator"] == 100
    assert result["L_vs_L"]["rate"] == pytest.approx(0.20)


def test_aggregate_excludes_unknown_handedness():
    """A player whose handedness did not resolve is skipped, not guessed."""
    rows = [(1, "vl", 100, 20, 110), (2, "vl", 100, 90, 110)]
    result = aggregate_platoon_baseline(rows, {1: "L"})  # id 2 absent
    assert result["L_vs_L"]["denominator"] == 100


def test_aggregate_ignores_unrequested_split_codes():
    """Codes other than vl/vr never reach a cell."""
    rows = [(1, "vl", 100, 20, 110), (1, "vlg", 500, 400, 550)]
    result = aggregate_platoon_baseline(rows, {1: "L"})
    assert result["L_vs_L"]["denominator"] == 100


def test_aggregate_zero_at_bats_yields_none_rate():
    """An empty cell reports no rate rather than dividing by zero."""
    result = aggregate_platoon_baseline([(1, "vl", 0, 0, 0)], {1: "L"})
    assert result["L_vs_L"]["rate"] is None
    assert result["L_vs_L"]["denominator"] == 0


def test_aggregate_handles_null_counts():
    """None counts from the API are treated as zero, not added blindly."""
    result = aggregate_platoon_baseline([(1, "vl", None, None, None)], {1: "L"})
    assert result["L_vs_L"]["denominator"] == 0


def test_aggregate_empty_input_is_empty():
    assert aggregate_platoon_baseline([], {}) == {}


# ---------------------------------------------------------------------------
# load_league_platoon_baseline — the checked-in file
# ---------------------------------------------------------------------------


def test_checked_in_baseline_loads_with_all_four_cells():
    """The committed data file parses and is complete, with no network call."""
    splits = load_league_platoon_baseline()
    assert set(splits) == set(PLATOON_CELLS)
    for cell in splits.values():
        assert 0.0 < cell["rate"] < 1.0
        assert cell["denominator"] > 0


def test_checked_in_baseline_shows_the_platoon_direction():
    """Each batter hand must hit better against the opposite pitcher hand.

    This is the property the 2x2 exists to capture, and the reason the pooled
    vs-L/vs-R figures are useless: pooled, the two hands cancel to within two
    thousandths of each other. If this assertion ever fails, the file was
    generated wrong — the numbers would be describing something else.
    """
    splits = load_league_platoon_baseline()
    assert splits["L_vs_R"]["rate"] > splits["L_vs_L"]["rate"]
    assert splits["R_vs_L"]["rate"] > splits["R_vs_R"]["rate"]


# ---------------------------------------------------------------------------
# load_league_platoon_baseline — degradation
# ---------------------------------------------------------------------------


def test_missing_baseline_file_returns_empty(tmp_path, capsys):
    """An absent file degrades to {} rather than raising."""
    assert load_league_platoon_baseline(tmp_path / "nope.json") == {}
    assert "unavailable" in capsys.readouterr().out


def test_malformed_baseline_file_returns_empty(tmp_path):
    """Unparseable JSON degrades to {}."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_league_platoon_baseline(bad) == {}


def test_incomplete_baseline_is_rejected_entirely(tmp_path, capsys):
    """A 2x2 missing a cell is unusable, not partially usable.

    Serving three cells would silently bias whichever matchups landed in the
    missing one, and nothing downstream would be able to tell.
    """
    partial = tmp_path / "partial.json"
    partial.write_text(
        json.dumps(
            {
                "splits": {
                    "L_vs_L": {"rate": 0.23, "denominator": 100},
                    "L_vs_R": {"rate": 0.25, "denominator": 100},
                    "R_vs_L": {"rate": 0.25, "denominator": 100},
                }
            }
        )
    )
    assert load_league_platoon_baseline(partial) == {}
    assert "incomplete" in capsys.readouterr().out


def test_baseline_without_splits_key_returns_empty(tmp_path):
    """A document lacking 'splits' entirely returns {}."""
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"season": 2026}))
    assert load_league_platoon_baseline(odd) == {}


# ---------------------------------------------------------------------------
# The Category 5 venue tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "loader,path",
    [(load_ballparks, BALLPARKS_PATH), (load_park_factors, PARK_FACTORS_PATH)],
)
def test_the_checked_in_table_loads_and_is_keyed_by_venue_id(loader, path):
    """Both files are committed, so this reads the real one rather than a fixture.

    Keys are MLB venue ids as strings, which is what makes the two tables
    joinable: Savant's own `venue_id` is the MLB venue id, verified across the 29
    venues the two tables share, with zero name mismatches (2026-08-09).
    """
    table = loader()
    assert len(table) >= 25
    assert all(key.isdigit() for key in table)


def test_the_two_venue_tables_agree_on_the_venues_they_share():
    """A silent key drift between them would make every CALC_31 read the wrong
    park while still returning a plausible index."""
    ballparks, factors = load_ballparks(), load_park_factors()
    shared = set(ballparks) & set(factors)
    assert len(shared) >= 25
    assert all(ballparks[key]["name"] == factors[key]["name"] for key in shared)


def test_the_roof_closed_grouping_covers_exactly_the_parks_with_a_roof():
    """Cross-validates the two independently generated tables against each other:
    Savant's roof-closed grouping and the Stats API's roofType are different
    sources for the same fact."""
    ballparks, factors = load_ballparks(), load_park_factors()
    shared = set(ballparks) & set(factors)
    roofed = {key for key in shared if ballparks[key]["roof_type"] != "Open"}
    has_closed = {key for key in shared if "roof_closed" in factors[key]["groupings"]}
    assert roofed == has_closed


def test_every_ballpark_carries_five_fence_distances():
    """CALC_33 maps spray sectors onto this list by position, so a short list
    would silently shift a park's right field onto its centre."""
    assert all(len(park["fences_ft"]) == 5 for park in load_ballparks().values())


@pytest.mark.parametrize("loader", [load_ballparks, load_park_factors])
def test_a_missing_venue_table_returns_empty_rather_than_raising(loader, tmp_path):
    assert loader(tmp_path / "absent.json") == {}


@pytest.mark.parametrize("loader", [load_ballparks, load_park_factors])
def test_a_corrupt_venue_table_returns_empty(loader, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert loader(bad) == {}


@pytest.mark.parametrize("loader", [load_ballparks, load_park_factors])
def test_a_venue_table_without_a_venues_mapping_returns_empty(loader, tmp_path):
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"generated": "2026-08-09"}))
    assert loader(odd) == {}


def test_a_partial_venue_table_is_kept_rather_than_rejected(tmp_path):
    """Unlike the platoon baseline, these tables are legitimately partial: Savant
    carried 29 of 30 venues. Rejecting the whole table would discard 29 good
    venues to punish one gap, and a per-venue lookup that misses already
    returns None on its own.
    """
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"venues": {"17": {"name": "Wrigley Field"}}}))
    assert set(load_park_factors(partial)) == {"17"}


# ---------------------------------------------------------------------------
# generate_park_factors.parse_leaderboard
# ---------------------------------------------------------------------------
#
# The generator's network half is never exercised here, only its pure parse.


def test_the_embedded_leaderboard_array_is_extracted():
    page = 'x<script> var data = [{"venue_id":"17","n_pa":"100"}]; </script>y'
    assert parse_leaderboard(page) == [{"venue_id": "17", "n_pa": "100"}]


@pytest.mark.parametrize("page", ["<html>no array here</html>", "var data = [];"])
def test_a_changed_page_raises_rather_than_writing_an_empty_table(page):
    """A silent empty table would be read as "every park is neutral" by every
    calculator downstream. This is a generator run by hand, so it fails loudly."""
    with pytest.raises(ValueError):
        parse_leaderboard(page)


# ---------------------------------------------------------------------------
# load_league_game_context and its generator's counting rule
# ---------------------------------------------------------------------------


def test_the_checked_in_game_context_carries_a_measured_skip_rate():
    """A genuine league census rather than a convenience sample, and from the
    Stats API rather than the broken bulk Statcast pull, so unlike every other
    league constant in this repo it does not inherit #38."""
    context = load_league_game_context()
    ninth = context["skipped_ninth"]
    assert ninth["denominator"] > 1000
    assert 0.3 < ninth["rate"] < 0.6
    assert ninth["skipped"] == pytest.approx(
        ninth["rate"] * ninth["denominator"], abs=1
    )


def test_the_game_context_carries_the_roadmaps_conditional_pa_loss():
    assert load_league_game_context()["pa_lost_per_skipped_ninth"] == 0.5


def test_a_missing_game_context_returns_empty(tmp_path, capsys):
    assert load_league_game_context(tmp_path / "absent.json") == {}
    assert "unavailable" in capsys.readouterr().out


def test_a_game_context_without_the_ninth_key_returns_empty(tmp_path, capsys):
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"season": 2026}))
    assert load_league_game_context(odd) == {}
    assert "malformed" in capsys.readouterr().out


def _game(state="Final", innings=9, home_ninth_runs=0):
    scored = [{"home": {"runs": 0}, "away": {"runs": 0}} for _ in range(innings - 1)]
    last = {"away": {"runs": 0}}
    if home_ninth_runs is not None:
        last["home"] = {"runs": home_ninth_runs}
    return {
        "status": {"abstractGameState": state},
        "linescore": {"innings": scored + [last]},
    }


def test_a_home_ninth_that_was_never_played_is_counted_as_skipped():
    """The signal is a home half-inning carrying no `runs` value at all."""
    result = skipped_ninth_rate([_game(home_ninth_runs=None), _game()])
    assert result == {"rate": 0.5, "denominator": 2, "skipped": 1}


def test_a_shortened_game_is_excluded_rather_than_scored_as_skipped():
    """A rain-shortened seven-inning game says nothing about the ninth."""
    assert (
        skipped_ninth_rate([_game(innings=7, home_ninth_runs=None)])["denominator"] == 0
    )


def test_an_unfinished_game_is_excluded():
    assert skipped_ninth_rate([_game(state="Live")])["denominator"] == 0


def test_extra_innings_still_count():
    """A game reaching the tenth still played a ninth."""
    assert skipped_ninth_rate([_game(innings=11)])["denominator"] == 1


def test_no_countable_games_yields_no_rate_rather_than_zero():
    assert skipped_ninth_rate([])["rate"] is None
