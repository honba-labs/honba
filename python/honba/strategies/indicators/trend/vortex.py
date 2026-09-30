"""Vortex indicator."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class VortexValue:
    plus: float
    minus: float


@indicator("vortex", "trend", inputs=("high", "low", "close"), outputs=("plus", "minus"),
           warmup=lambda s: s.period + 1)
class Vortex(Indicator):
    """Vortex: VI+ = sum|high - prev low| / sum TR, VI- = sum|low - prev high| / sum TR over ``period`` bars.

    Needs a previous bar, so the first value appears after ``period + 1`` bars. Zero TR sum gives 0.
    """

    def __init__(self, period: int = 14) -> None:
        self.period = _check(period)
        self._w: deque[tuple[float, float, float]] = deque(maxlen=period)
        self._prev: tuple[float, float, float] | None = None

    def update(self, high: float, low: float, close: float) -> VortexValue | None:
        prev, self._prev = self._prev, (high, low, close)
        if prev is None:
            return None
        ph, pl, pc = prev
        self._w.append((abs(high - pl), abs(low - ph), max(high - low, abs(high - pc), abs(low - pc))))
        if len(self._w) < self.period:
            return None
        tr = sum(t for _, _, t in self._w)
        if tr <= 0:
            return VortexValue(0.0, 0.0)
        return VortexValue(sum(a for a, _, _ in self._w) / tr, sum(b for _, b, _ in self._w) / tr)
