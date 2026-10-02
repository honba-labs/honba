"""Chaikin oscillator."""

from __future__ import annotations

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.moving_average import Ema
from .accumulation_distribution import AccumulationDistribution


@indicator(
    "chaikin_oscillator",
    "volume",
    inputs=("high", "low", "close", "volume"),
    warmup=lambda s: max(s.fast, s.slow),
)
class ChaikinOscillator(Indicator):
    """Chaikin oscillator: EMA(A/D line, fast) - EMA(A/D line, slow), SMA-seeded EMAs (TradingView)."""

    def __init__(self, fast: int = 3, slow: int = 10) -> None:
        self.fast, self.slow = _check(fast), _check(slow)
        self._ad = AccumulationDistribution()
        self._f, self._s = Ema(fast), Ema(slow)

    def update(self, high: float, low: float, close: float, volume: float) -> float | None:
        ad = self._ad.update(high, low, close, volume)
        f, s = self._f.update(ad), self._s.update(ad)
        return None if f is None or s is None else f - s
