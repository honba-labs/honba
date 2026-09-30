from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

@indicator("williams_r", "momentum", inputs=("high", "low", "close"), warmup=lambda s: s.length)
class WilliamsR(Indicator):
    """Williams %R (TradingView): 100 * (close - highest high) / (highest high - lowest low), range -100..0.

    A flat window (zero range) returns -50 (midpoint).
    """

    def __init__(self, length: int = 14) -> None:
        self.length = _check(length)
        self._h: deque[float] = deque(maxlen=length)
        self._l: deque[float] = deque(maxlen=length)

    def update(self, high: float, low: float, close: float) -> float | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self.length:
            return None
        hh, ll = max(self._h), min(self._l)
        return 100.0 * (close - hh) / (hh - ll) if hh != ll else -50.0
