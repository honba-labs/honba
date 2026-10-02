from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

from honba.strategies.indicators.moving_average import Sma


@dataclass(slots=True)
class StochasticValue:
    k: float
    d: float


@indicator(
    "stochastic",
    "momentum",
    inputs=("high", "low", "close"),
    outputs=("k", "d"),
    warmup=lambda s: s.k_length + s.k_smooth + s.d_smooth - 2,
)
class Stochastic(Indicator):
    """Stochastic oscillator (TradingView): raw %K over ``k_length`` bars, %K = SMA(raw, k_smooth), %D = SMA(%K, d_smooth).

    raw = 100 * (close - lowest low) / (highest high - lowest low); a flat window (zero range) gives 0.
    """

    def __init__(self, k_length: int = 14, k_smooth: int = 1, d_smooth: int = 3) -> None:
        self.k_length = _check(k_length)
        self.k_smooth = _check(k_smooth)
        self.d_smooth = _check(d_smooth)
        self._h: deque[float] = deque(maxlen=k_length)
        self._l: deque[float] = deque(maxlen=k_length)
        self._k, self._d = Sma(k_smooth), Sma(d_smooth)

    def update(self, high: float, low: float, close: float) -> StochasticValue | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self.k_length:
            return None
        hh, ll = max(self._h), min(self._l)
        k = self._k.update(100.0 * (close - ll) / (hh - ll) if hh != ll else 0.0)
        d = self._d.update(k) if k is not None else None
        return StochasticValue(k, d) if d is not None else None
