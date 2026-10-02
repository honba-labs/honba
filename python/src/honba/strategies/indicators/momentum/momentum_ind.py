from __future__ import annotations

from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@indicator("momentum", "momentum", warmup=lambda s: s.length + 1)
class Momentum(Indicator):
    """Momentum (TradingView): x - x[length] (price difference, not percent)."""

    def __init__(self, length: int = 10) -> None:
        self.length = _check(length)
        self._w: deque[float] = deque(maxlen=length + 1)

    def update(self, x: float) -> float | None:
        self._w.append(x)
        return x - self._w[0] if len(self._w) == self.length + 1 else None
