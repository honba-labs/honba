"""Price volume trend."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


@indicator("price_volume_trend", "volume", inputs=("close", "volume"), warmup=lambda s: 1)
class PriceVolumeTrend(Indicator):
    """Price volume trend: cumulative (close - prev_close) / prev_close * volume (first bar = 0; term 0 if prev_close is 0)."""

    def __init__(self) -> None:
        self._prev: float | None = None
        self.value = 0.0

    def update(self, close: float, volume: float) -> float:
        if self._prev:
            self.value += (close - self._prev) / self._prev * volume
        self._prev = close
        return self.value
