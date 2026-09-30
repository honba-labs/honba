"""Triple exponential moving average."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Ema


@indicator("tema", "moving_average", warmup=lambda s: 3 * s.period - 2)
class Tema(Indicator):
    """TEMA = 3*EMA1 - 3*EMA2 + EMA3 (EMA of EMA of EMA); SMA-seeded EMAs."""

    def __init__(self, period: int = 9) -> None:
        self.period = _check(period)
        self._e1, self._e2, self._e3 = Ema(period), Ema(period), Ema(period)

    def update(self, x: float) -> float | None:
        e1 = self._e1.update(x)
        if e1 is None:
            return None
        e2 = self._e2.update(e1)
        if e2 is None:
            return None
        e3 = self._e3.update(e2)
        return None if e3 is None else 3.0 * e1 - 3.0 * e2 + e3
