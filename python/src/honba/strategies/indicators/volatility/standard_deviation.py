"""Rolling standard deviation (volatility family)."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.volatility._stats import RollingStd


@indicator("standard_deviation", "volatility", warmup=lambda s: s.length)
class StandardDeviation(Indicator):
    """Population (biased, divide by n) standard deviation of close over ``length`` bars (TradingView ta.stdev)."""

    def __init__(self, length: int = 5) -> None:
        self.length = _check(length)
        self._s = RollingStd(length)

    def update(self, close: float) -> float | None:
        r = self._s.update(close)
        return None if r is None else r[1]
