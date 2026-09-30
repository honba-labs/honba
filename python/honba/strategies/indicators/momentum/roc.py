from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

@indicator("roc", "momentum", warmup=lambda s: s.length + 1)
class Roc(Indicator):
    """Rate of change (TradingView): 100 * (x - x[length]) / x[length], in percent.

    A zero base value gives 0.
    """

    def __init__(self, length: int = 9) -> None:
        self.length = _check(length)
        self._w: deque[float] = deque(maxlen=length + 1)

    def update(self, x: float) -> float | None:
        self._w.append(x)
        if len(self._w) < self.length + 1:
            return None
        base = self._w[0]
        return 100.0 * (x - base) / base if base != 0 else 0.0
