"""Small pure statistics shared by weighting and scoring."""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

from honba.entities.instrument import InstrumentId
from honba.strategies.portfolio.view import MarketView


def return_stdev(view: MarketView, instrument_id: InstrumentId, lookback: int) -> float | None:
    """Sample stdev of simple returns over the last ``lookback`` closes, else ``None``.

    ``lookback`` closes give ``lookback - 1`` returns; at least two returns are needed, so
    ``lookback=2`` is always ``None``. ``None`` also when history is shorter than
    ``lookback``, any close is non-finite, or any return's base close is <= 0.
    """
    closes = view.recent_closes(instrument_id)
    if len(closes) < lookback:
        return None
    window = closes[-lookback:]
    if not all(math.isfinite(c) for c in window) or any(c <= 0 for c in window[:-1]):
        return None
    returns = [b / a - 1.0 for a, b in pairwise(window)]
    if len(returns) < 2:
        return None
    return statistics.stdev(returns)
