"""Volatility stop (volatility family)."""
from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.volatility.atr import Atr


@dataclass(frozen=True, slots=True)
class VolatilityStopValue:
    value: float
    direction: float


@indicator("volatility_stop", "volatility", inputs=("high", "low", "close"), outputs=("value", "direction"),
           warmup=lambda s: s.length)
class VolatilityStop(Indicator):
    """ATR trailing volatility stop (TradingView 'Volatility Stop'); direction +1 = uptrend (stop below), -1 = downtrend.

    Stop trails the extreme close since the last flip by ``mult * ATR`` (Wilder ATR, first TR = high - low);
    it only ratchets in the trend direction and flips when close crosses it. Starts as an uptrend at the first ATR bar."""

    def __init__(self, length: int = 20, mult: float = 2.0) -> None:
        self.length = _check(length)
        if mult <= 0:
            raise ValueError("mult must be > 0")
        self.mult = mult
        self._atr = Atr(length, include_first_bar=True)
        self._started = False
        self._up = True
        self._max = self._min = self._stop = 0.0

    def update(self, high: float, low: float, close: float) -> VolatilityStopValue | None:
        atr = self._atr.update(high, low, close)
        if atr is None:
            return None
        m = self.mult * atr
        if not self._started:
            self._started = True
            self._max = self._min = close
            self._stop = close - m  # uptrend seed: below the close by mult * ATR
        self._max, self._min = max(self._max, close), min(self._min, close)
        if self._up:
            self._stop = max(self._stop, self._max - m)
        else:
            self._stop = min(self._stop, self._min + m)
        up = close - self._stop >= 0
        if up != self._up:
            self._max = self._min = close
            self._stop = close - m if up else close + m
            self._up = up
        return VolatilityStopValue(self._stop, 1.0 if self._up else -1.0)
