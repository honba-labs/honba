"""Chaikin volatility (volatility family)."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Ema


@indicator("chaikin_volatility", "volatility", inputs=("high", "low"), warmup=lambda s: s.length + s.roc_length)
class ChaikinVolatility(Indicator):
    """Chaikin volatility: percent rate of change over ``roc_length`` of the SMA-seeded EMA(high - low, length).

    A zero EMA ``roc_length`` bars ago returns 0.0."""

    def __init__(self, length: int = 10, roc_length: int = 10) -> None:
        self.length, self.roc_length = _check(length), _check(roc_length)
        self._ema = Ema(length)
        self._hist: list[float] = []

    def update(self, high: float, low: float) -> float | None:
        e = self._ema.update(high - low)
        if e is None:
            return None
        self._hist.append(e)
        if len(self._hist) > self.roc_length + 1:
            self._hist.pop(0)
        if len(self._hist) <= self.roc_length:
            return None
        old = self._hist[0]
        return 0.0 if old == 0.0 else 100.0 * (e - old) / old
