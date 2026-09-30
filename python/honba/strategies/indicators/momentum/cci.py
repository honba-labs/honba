from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

@indicator("cci", "momentum", inputs=("high", "low", "close"), warmup=lambda s: s.length)
class Cci(Indicator):
    """Commodity Channel Index (TradingView): (tp - SMA(tp)) / (0.015 * mean absolute deviation), tp = (high+low+close)/3.

    Uses the mean absolute deviation about the SMA (not standard deviation). Zero deviation gives 0.
    """

    def __init__(self, length: int = 20) -> None:
        self.length = _check(length)
        self._w: deque[float] = deque(maxlen=length)

    def update(self, high: float, low: float, close: float) -> float | None:
        self._w.append((high + low + close) / 3.0)
        if len(self._w) < self.length:
            return None
        n = self.length
        mean = sum(self._w) / n
        mad = sum(abs(v - mean) for v in self._w) / n
        return (self._w[-1] - mean) / (0.015 * mad) if mad != 0 else 0.0
