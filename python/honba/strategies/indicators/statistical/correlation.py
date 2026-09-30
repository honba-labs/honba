"""Rolling Pearson correlation of price with a benchmark (statistical family)."""
from __future__ import annotations

import math

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingPairMoments


@indicator("correlation", "statistical", inputs=("close", "benchmark"), warmup=lambda s: s.length)
class Correlation(Indicator):
    """Pearson correlation of close vs benchmark PRICES over ``length`` bars (TradingView ta.correlation).

    Returns 0.0 when either window has zero variance. O(1) per update (``RollingPairMoments``);
    NaN while a non-finite or ``|v| > 1e150`` value is in either window (the old O(n) code
    returned 1.0 there by accident of ``min``/``max`` with NaN), exact again once it has left.
    """

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._rp = RollingPairMoments(length)

    def update(self, close: float, benchmark: float) -> float | None:
        m = self._rp.update(close, benchmark)
        if m is None:
            return None
        if math.isnan(m.cov):
            return math.nan
        if m.var_x <= 0 or m.var_y <= 0:
            return 0.0
        return max(-1.0, min(1.0, m.cov / math.sqrt(m.var_x * m.var_y)))
