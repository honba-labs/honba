"""Volume-weighted moving average."""
from __future__ import annotations

from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@indicator("vwma", "moving_average", inputs=("close", "volume"), warmup=lambda s: s.period)
class Vwma(Indicator):
    """VWMA: sum(close*volume) / sum(volume) over ``period`` bars.

    Convention: if the window's total volume is 0, returns the latest close.
    """

    def __init__(self, period: int = 20) -> None:
        self.period = _check(period)
        self._w: deque[tuple[float, float]] = deque(maxlen=period)

    def update(self, close: float, volume: float) -> float | None:
        self._w.append((close, volume))
        if len(self._w) < self.period:
            return None
        vol = sum(v for _, v in self._w)
        if vol == 0.0:
            return float(close)
        return sum(c * v for c, v in self._w) / vol
