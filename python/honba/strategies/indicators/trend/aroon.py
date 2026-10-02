"""Aroon."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class AroonValue:
    up: float
    down: float
    oscillator: float


@indicator(
    "aroon",
    "trend",
    inputs=("high", "low"),
    outputs=("up", "down", "oscillator"),
    warmup=lambda s: s.length + 1,
)
class Aroon(Indicator):
    """Aroon over the last ``length + 1`` bars: up = 100*(length - bars since highest high)/length
    (down likewise with lowest low), oscillator = up - down. Ties resolve to the most recent bar.
    """

    def __init__(self, length: int = 14) -> None:
        self.length = _check(length)
        self._h: deque[float] = deque(maxlen=length + 1)
        self._l: deque[float] = deque(maxlen=length + 1)

    def update(self, high: float, low: float) -> AroonValue | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) <= self.length:
            return None
        n = self.length
        hh, ll = max(self._h), min(self._l)
        since_h = next(i for i, v in enumerate(reversed(self._h)) if v == hh)
        since_l = next(i for i, v in enumerate(reversed(self._l)) if v == ll)
        up, down = 100.0 * (n - since_h) / n, 100.0 * (n - since_l) / n
        return AroonValue(up, down, up - down)
