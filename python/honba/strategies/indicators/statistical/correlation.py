"""Rolling Pearson correlation of price with a benchmark (statistical family)."""
from __future__ import annotations

import math
from collections import deque

from honba.strategies.indicators.statistical._pair import cov_var
from honba.strategies.indicators._base import Indicator, indicator


@indicator("correlation", "statistical", inputs=("close", "benchmark"), warmup=lambda s: s.length)
class Correlation(Indicator):
    """Pearson correlation of close vs benchmark PRICES over ``length`` bars (TradingView ta.correlation).

    Returns 0.0 when either window has zero variance.
    """

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._x: deque[float] = deque(maxlen=length)
        self._y: deque[float] = deque(maxlen=length)

    def update(self, close: float, benchmark: float) -> float | None:
        self._x.append(close)
        self._y.append(benchmark)
        if len(self._x) < self.length:
            return None
        c, vx, vy = cov_var(self._x, self._y, 0)
        if vx <= 0 or vy <= 0:
            return 0.0
        return max(-1.0, min(1.0, c / math.sqrt(vx * vy)))
