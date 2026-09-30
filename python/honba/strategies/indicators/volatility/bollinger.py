"""Bollinger bands (volatility family)."""
from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingMoments
from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class BollingerValue:
    upper: float
    middle: float
    lower: float


@indicator("bollinger", "volatility", outputs=("upper", "middle", "lower"), warmup=lambda s: s.period)
class Bollinger(Indicator):
    """Bands at ``mult`` (upper) and ``mult_lower`` (default ``mult``) population
    standard deviations around the SMA."""

    def __init__(self, period: int = 20, mult: float = 2.0, mult_lower: float | None = None) -> None:
        self.period, self.mult = _check(period), mult
        self.mult_lower = mult if mult_lower is None else mult_lower
        if mult < 0 or self.mult_lower < 0:
            raise ValueError("band multipliers must be >= 0")
        self._m = RollingMoments(period)

    def update(self, x: float) -> BollingerValue | None:
        r = self._m.update(x)
        if r is None:
            return None
        return BollingerValue(r.mean + self.mult * r.std, r.mean, r.mean - self.mult_lower * r.std)
