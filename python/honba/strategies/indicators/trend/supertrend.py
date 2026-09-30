"""SuperTrend."""
from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.volatility.atr import Atr


@dataclass(frozen=True, slots=True)
class SupertrendValue:
    value: float
    direction: float  # +1 uptrend (value is the lower band), -1 downtrend (upper band)


@indicator("supertrend", "trend", inputs=("high", "low", "close"), outputs=("value", "direction"),
           warmup=lambda s: s.atr_period)
class Supertrend(Indicator):
    """SuperTrend (TradingView): bands = hl2 -/+ factor*ATR (Wilder, first TR = high-low),
    ratcheted against the previous close; direction flips when close crosses the active band.

    The first value starts in a downtrend (TradingView convention), after ``atr_period`` bars.
    """

    def __init__(self, atr_period: int = 10, factor: float = 3.0) -> None:
        if factor <= 0:
            raise ValueError(f"factor must be > 0, got {factor}")
        self.atr_period, self.factor = atr_period, float(factor)
        self._atr = Atr(atr_period, include_first_bar=True)
        self._prev_close: float | None = None
        self._lower = self._upper = None
        self._dir = 0

    def update(self, high: float, low: float, close: float) -> SupertrendValue | None:
        pc, self._prev_close = self._prev_close, close
        atr = self._atr.update(high, low, close)
        if atr is None:
            return None
        mid = (high + low) / 2.0
        lower, upper = mid - self.factor * atr, mid + self.factor * atr
        if self._dir == 0:
            self._dir = -1
        else:
            if not (lower > self._lower or pc < self._lower):
                lower = self._lower
            if not (upper < self._upper or pc > self._upper):
                upper = self._upper
            if self._dir == -1:
                self._dir = 1 if close > upper else -1
            else:
                self._dir = -1 if close < lower else 1
        self._lower, self._upper = lower, upper
        return SupertrendValue(lower if self._dir == 1 else upper, float(self._dir))
