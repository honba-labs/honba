"""Choppiness index (volatility family)."""

from __future__ import annotations

import math
from collections import deque

from honba.strategies.indicators._base import Indicator, indicator


@indicator(
    "choppiness_index", "volatility", inputs=("high", "low", "close"), warmup=lambda s: s.length
)
class ChoppinessIndex(Indicator):
    """Choppiness index = 100 * log10(sum(TR, n) / (highest high - lowest low)) / log10(n).

    The first bar's true range is high - low (TradingView). Values near 100 are choppy, near 0 trending.
    A zero-range window returns 0.0. ``length`` must be >= 2."""

    def __init__(self, length: int = 14) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._prev: float | None = None
        self._tr: deque[float] = deque(maxlen=length)
        self._h: deque[float] = deque(maxlen=length)
        self._l: deque[float] = deque(maxlen=length)

    def update(self, high: float, low: float, close: float) -> float | None:
        prev, self._prev = self._prev, close
        tr = high - low if prev is None else max(high - low, abs(high - prev), abs(low - prev))
        self._tr.append(tr)
        self._h.append(high)
        self._l.append(low)
        if len(self._tr) < self.length:
            return None
        rng = max(self._h) - min(self._l)
        total = sum(self._tr)
        if rng <= 0.0 or total <= 0.0:
            return 0.0
        return 100.0 * math.log10(total / rng) / math.log10(self.length)
