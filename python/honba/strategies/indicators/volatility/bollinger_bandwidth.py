"""Bollinger bandwidth (volatility family)."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.volatility._stats import RollingStd


@indicator("bollinger_bandwidth", "volatility", warmup=lambda s: s.period)
class BollingerBandwidth(Indicator):
    """Bollinger bandwidth in percent = 100 * (upper - lower) / middle (population stdev).

    A zero middle band returns 0.0."""

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
        return 0.0 if mean == 0.0 else 100.0 * 2 * self.mult * sd / mean
