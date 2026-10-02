"""On-balance volume."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


@indicator("obv", "volume", inputs=("close", "volume"), warmup=lambda s: 1)
class Obv(Indicator):
    """On-balance volume: running sum of +volume on up-closes, -volume on down-closes, 0 if unchanged (first bar = 0)."""

    def __init__(self) -> None:
        self._prev: float | None = None
        self.value = 0.0

    def update(self, close: float, volume: float) -> float:
        if self._prev is not None:
            if close > self._prev:
                self.value += volume
            elif close < self._prev:
                self.value -= volume
        self._prev = close
        return self.value
