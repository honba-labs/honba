"""Bollinger %B (volatility family)."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.volatility._stats import RollingStd


@indicator("bollinger_percent_b", "volatility", warmup=lambda s: s.period)
class BollingerPercentB(Indicator):
    """Bollinger %B = (close - lower) / (upper - lower), SMA +/- mult population stdev.

    0 = at the lower band, 1 = at the upper band. Zero-width bands (flat window) return 0.5."""

    def __init__(self, period: int = 20, mult: float = 2.0) -> None:
        self.period = _check(period)
        if mult < 0:
            raise ValueError("mult must be >= 0")
        self.mult = mult
        self._s = RollingStd(period)

    def update(self, close: float) -> float | None:
        r = self._s.update(close)
        if r is None:
            return None
        mean, sd = r
        width = 2 * self.mult * sd
        if width == 0.0:
            return 0.5
        return (close - (mean - self.mult * sd)) / width
