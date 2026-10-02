"""Rolling Pearson correlation of price with a benchmark (statistical family)."""

from __future__ import annotations

import math

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingPairMoments


@indicator("correlation", "statistical", inputs=("close", "benchmark"), warmup=lambda s: s.length)
class Correlation(Indicator):
    """Pearson correlation of close vs benchmark PRICES over ``length`` bars.

    Matches TradingView ``ta.correlation``.
    Returns 0.0 when either window has zero variance. O(1) per update (``RollingPairMoments``);
    NaN while a non-finite or ``|v| > 1e150`` value is in either window (the old O(n) code
    returned 1.0 there by accident of ``min``/``max`` with NaN), exact again once it has left.
    """

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._rp = RollingPairMoments(length)
        self._update = self._rp.update_raw

    def update(self, close: float, benchmark: float) -> float | None:
        m = self._update(close, benchmark)
        if m is None:
            return None
        cov, vx, vy = m[4], m[2], m[3]
        if cov != cov:
            return math.nan
        if vx <= 0 or vy <= 0:
            return 0.0
        r = cov / math.sqrt(vx * vy)
        return 1.0 if r > 1.0 else -1.0 if r < -1.0 else r
