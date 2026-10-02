"""Kaufman adaptive moving average."""

from __future__ import annotations

from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@indicator("kama", "moving_average", warmup=lambda s: s.period + 1)
class Kama(Indicator):
    """KAMA: er=|c-c[n]|/sum|dc|; sc=(er*(2/(fast+1)-2/(slow+1))+2/(slow+1))^2; k += sc*(c-k).

    Seeded with the close just before the first computed bar, so the first value
    appears after ``period + 1`` bars. Zero total movement gives er = 0.
    """

    def __init__(self, period: int = 10, fast: int = 2, slow: int = 30) -> None:
        self.period = _check(period)
        if not 0 < _check(fast) < _check(slow):
            raise ValueError(f"fast must be less than slow, got {fast} and {slow}")
        self.fast, self.slow = fast, slow
        self._fa, self._sa = 2.0 / (fast + 1), 2.0 / (slow + 1)
        self._w: deque[float] = deque(maxlen=period + 1)
        self._path = 0.0  # sum of |diff| over the last `period` steps
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        w = self._w
        if len(w) == self.period + 1:
            self._path -= abs(w[1] - w[0])
        if w:
            self._path += abs(x - w[-1])
        w.append(x)
        if len(w) < self.period + 1:
            return None
        er = abs(x - w[0]) / self._path if self._path > 1e-12 else 0.0
        sc = (er * (self._fa - self._sa) + self._sa) ** 2
        prev = w[-2] if self.value is None else self.value
        self.value = prev + sc * (x - prev)
        return self.value
