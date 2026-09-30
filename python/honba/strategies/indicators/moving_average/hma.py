"""Hull moving average."""
from __future__ import annotations

import math

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Wma


@indicator("hma", "moving_average", warmup=lambda s: s.period + s._root - 1)
class Hma(Indicator):
    """Hull MA: WMA(2*WMA(x, n//2) - WMA(x, n), floor(sqrt(n))) (TradingView ta.hma)."""

    def __init__(self, period: int = 9) -> None:
        self.period = _check(period)
        self._root = max(1, int(math.sqrt(period)))
        self._half = Wma(max(1, period // 2))
        self._full = Wma(period)
        self._out = Wma(self._root)

    def update(self, x: float) -> float | None:
        h, f = self._half.update(x), self._full.update(x)
        if f is None:
            return None
        return self._out.update(2.0 * h - f)
