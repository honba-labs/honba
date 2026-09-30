from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

@indicator("cmo", "momentum", warmup=lambda s: s.length + 1)
class ChandeMomentum(Indicator):
    """Chande Momentum Oscillator (TradingView): 100 * (sum of gains - sum of losses) / (sum of gains + sum of losses) over ``length`` changes.

    Simple sums (no Wilder smoothing); range -100..100. No movement at all gives 0.
    """

    def __init__(self, length: int = 9) -> None:
        self.length = _check(length)
        self._prev: float | None = None
        self._w: deque[float] = deque(maxlen=length)

    def update(self, close: float) -> float | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        self._w.append(close - prev)
        if len(self._w) < self.length:
            return None
        up = sum(c for c in self._w if c > 0)
        dn = sum(-c for c in self._w if c < 0)
        return 100.0 * (up - dn) / (up + dn) if up + dn != 0 else 0.0
