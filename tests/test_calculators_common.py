from calculators import aggregate_lines


# ---- aggregate_lines ----


def test_aggregate_lines_tolerates_missing_and_null_fields():
    assert aggregate_lines([{"hits": 2}, {"hits": None, "atBats": 3}]) == {
        "plateAppearances": 0,
        "atBats": 3,
        "hits": 2,
        "strikeOuts": 0,
        "baseOnBalls": 0,
    }
