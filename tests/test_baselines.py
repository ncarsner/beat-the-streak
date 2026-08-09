"""Tests for the league-baseline loader and the generator's aggregation.

The generator's network half is never exercised here — only its pure
aggregation, which is why that function was split out of main().
"""

import json

import pytest

from calculators.baselines import (
    PLATOON_CELLS,
    load_league_platoon_baseline,
)
from scripts.generate_league_platoon_baseline import aggregate_platoon_baseline


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
