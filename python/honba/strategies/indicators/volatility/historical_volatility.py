"""Historical volatility (volatility family)."""
from __future__ import annotations

import math

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._india import TRADING_DAYS_PER_YEAR
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.volatility._stats import RollingStd


@indicator("historical_volatility", "volatility", warmup=lambda s: s.length + 1)
class HistoricalVolatility(Indicator):
    """Annualised historical volatility in percent: 100 * stdev(ln(close/prev), length) * sqrt(periods_per_year).

    Population stdev of log returns. ``periods_per_year`` defaults to 252 NSE/BSE trading days
    (pass bars per year for intraday data). A non-positive price yields a 0.0 log return."""

    def __init__(self, length: int = 10, periods_per_year: float = TRADING_DAYS_PER_YEAR) -> None:
        self.length = _check(length)
        if periods_per_year <= 0:
            raise ValueError("periods_per_year must be > 0")
        self.periods_per_year = periods_per_year
        self._prev: float | None = None
        self._s = RollingStd(length)

    def update(self, close: float) -> float | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        r = math.log(close / prev) if close > 0 and prev > 0 else 0.0
        out = self._s.update(r)
        return None if out is None else 100.0 * out[1] * math.sqrt(self.periods_per_year)
