"""Shared rolling helpers for the volatility family (O(window) per update, numerically stable)."""
from __future__ import annotations

import math
from collections import deque


class RollingStd:
    """Population (biased, /n) standard deviation and mean over the last ``period`` values."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._w: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> tuple[float, float] | None:
        """Returns ``(mean, stdev)`` once ``period`` values are seen, else None."""
        self._w.append(x)
        if len(self._w) < self.period:
            return None
        mean = sum(self._w) / self.period
        var = sum((v - mean) ** 2 for v in self._w) / self.period
        return mean, math.sqrt(var)
