"""Double exponential moving average."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Ema


@indicator("dema", "moving_average", warmup=lambda s: 2 * s.period - 1)
class Dema(Indicator):
    """DEMA = 2*EMA1 - EMA2 where EMA2 = EMA(EMA1); SMA-seeded EMAs, alpha = 2/(n+1)."""

    def __init__(self, period: int = 9) -> None:
        self.period = _check(period)
        self._e1, self._e2 = Ema(period), Ema(period)

    def update(self, x: float) -> float | None:
        e1 = self._e1.update(x)
        if e1 is None:
            return None
        e2 = self._e2.update(e1)
        return None if e2 is None else 2.0 * e1 - e2
