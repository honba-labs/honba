"""Vortex indicator."""

from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingSum
from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class VortexValue:
    plus: float
    minus: float


@indicator(
    "vortex",
    "trend",
    inputs=("high", "low", "close"),
    outputs=("plus", "minus"),
    warmup=lambda s: s.period + 1,
)
class Vortex(Indicator):
    """Vortex: VI+ = sum|high - prev low| / sum TR, VI- = sum|low - prev high| / sum TR over ``period`` bars.

    Needs a previous bar, so the first value appears after ``period + 1`` bars. Zero TR sum gives 0.
    """

    def __init__(self, period: int = 14) -> None:
        self.period = _check(period)
        self._prev: tuple[float, float, float] | None = None
        self._plus = RollingSum(period)
        self._minus = RollingSum(period)
        self._tr = RollingSum(period)

    def update(self, high: float, low: float, close: float) -> VortexValue | None:
        prev, self._prev = self._prev, (high, low, close)
        if prev is None:
            return None
        ph, pl, pc = prev
        p = self._plus.update(abs(high - pl))
        m = self._minus.update(abs(low - ph))
        t = self._tr.update(max(high - low, abs(high - pc), abs(low - pc)))
        if p is None or m is None or t is None:
            return None
        if t <= 0:
            return VortexValue(0.0, 0.0)
        return VortexValue(p / t, m / t)
