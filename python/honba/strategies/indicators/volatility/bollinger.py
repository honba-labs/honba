"""Bollinger bands (volatility family)."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class BollingerValue:
    upper: float
    middle: float
    lower: float


class Bollinger:
    """Bands at ``mult`` (upper) and ``mult_lower`` (default ``mult``) population
    standard deviations around the SMA."""

    def __init__(self, period: int = 20, mult: float = 2.0, mult_lower: float | None = None) -> None:
        self.period, self.mult = _check(period), mult
        self.mult_lower = mult if mult_lower is None else mult_lower
        if mult < 0 or self.mult_lower < 0:
            raise ValueError("band multipliers must be >= 0")
        self._w: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> BollingerValue | None:
        self._w.append(x)
        if len(self._w) < self.period:
            return None
        mean = sum(self._w) / self.period
        sd = math.sqrt(sum((v - mean) ** 2 for v in self._w) / self.period)
        return BollingerValue(mean + self.mult * sd, mean, mean - self.mult_lower * sd)
