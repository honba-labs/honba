"""Ease of movement."""
from __future__ import annotations

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.moving_average import Sma


@indicator("ease_of_movement", "volume", inputs=("high", "low", "close", "volume"), warmup=lambda s: s.period + 1)
class EaseOfMovement(Indicator):
    """Ease of movement (TradingView): SMA(divisor * change(hl2) * (high-low) / volume, n); a bar with zero volume contributes 0."""

    def __init__(self, period: int = 14, divisor: float = 10000.0) -> None:
        self.period = _check(period)
        if divisor <= 0:
            raise ValueError(f"divisor must be > 0, got {divisor}")
        self.divisor = float(divisor)
        self._sma = Sma(period)
        self._prev: float | None = None

    def update(self, high: float, low: float, close: float, volume: float) -> float | None:
        hl2 = (high + low) / 2.0
        prev, self._prev = self._prev, hl2
        if prev is None:
            return None
        eom = self.divisor * (hl2 - prev) * (high - low) / volume if volume else 0.0
        return self._sma.update(eom)
