"""Rolling z-score (statistical family)."""
from __future__ import annotations

import math
from collections import deque

from honba.strategies.indicators._base import Indicator, indicator


@indicator("zscore", "statistical", warmup=lambda s: s.length)
class ZScore(Indicator):
    """(close - SMA) / population stdev over ``length`` bars (like ta.stdev, biased); 0.0 if stdev is 0."""

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._w: deque[float] = deque(maxlen=length)

    def update(self, close: float) -> float | None:
        self._w.append(close)
        if len(self._w) < self.length:
            return None
        m = sum(self._w) / self.length
        sd = math.sqrt(sum((v - m) ** 2 for v in self._w) / self.length)
        return (close - m) / sd if sd > 0 else 0.0
