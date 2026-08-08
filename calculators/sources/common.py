"""Helpers shared across all source modules.

Nothing here touches the network — only utilities needed by multiple
source modules, such as pandas-sentinel cleaning.
"""


def _clean(value, isna):
    """Convert any pandas missing-value sentinel to None, leaving the rest alone.

    Takes pandas' own `isna` rather than testing for NaN by hand: pandas has
    more than one missing sentinel, and they are not interchangeable. `np.nan`
    is a float that fails an equality check against itself, but `pd.NA` — what a
    nullable string column yields, and `events` is unset on every pitch that
    does not end a plate appearance — is neither a float nor comparable, so a
    hand-rolled NaN check passes it straight through to calculators promised
    they would only ever see None.
    """
    if value is None:
        return None
    try:
        missing = bool(isna(value))
    except (TypeError, ValueError):
        return value
    return None if missing else value
