from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

from honba.strategies.indicators.momentum.roc import Roc
from honba.strategies.indicators.moving_average import Wma


@indicator(
    "coppock_curve", "momentum", warmup=lambda s: max(s.long_roc, s.short_roc) + s.wma_length
)
class CoppockCurve(Indicator):
    """Coppock Curve (TradingView): WMA(ROC(long_roc) + ROC(short_roc), wma_length), ROC in percent."""

    def __init__(self, wma_length: int = 10, long_roc: int = 14, short_roc: int = 11) -> None:
        self.wma_length = _check(wma_length)
        self.long_roc = _check(long_roc)
        self.short_roc = _check(short_roc)
        self._a, self._b = Roc(long_roc), Roc(short_roc)
        self._wma = Wma(wma_length)

    def update(self, close: float) -> float | None:
        a, b = self._a.update(close), self._b.update(close)
        if a is None or b is None:
            return None
        return self._wma.update(a + b)
