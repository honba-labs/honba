from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@dataclass(slots=True)
class FisherValue:
    fisher: float
    trigger: float


@indicator(
    "fisher_transform",
    "momentum",
    inputs=("high", "low"),
    outputs=("fisher", "trigger"),
    warmup=lambda s: s.length,
)
class FisherTransform(Indicator):
    """Fisher Transform (TradingView): fisher = 0.5*ln((1+v)/(1-v)) + 0.5*fisher[1]; trigger = fisher[1].

    v = clamp(0.66*((hl2 - low_n)/max(high_n - low_n, 0.001) - 0.5) + 0.67*v[1], -0.999, 0.999), hl2 = (high+low)/2,
    high_n/low_n the extremes of hl2 over ``length``. Previous values start at 0 (Pine ``nz``).
    """

    def __init__(self, length: int = 9) -> None:
        self.length = _check(length)
        self._w: deque[float] = deque(maxlen=length)
        self._v = 0.0
        self._fish = 0.0

    def update(self, high: float, low: float) -> FisherValue | None:
        hl2 = (high + low) / 2.0
        self._w.append(hl2)
        if len(self._w) < self.length:
            return None
        hi, lo = max(self._w), min(self._w)
        v = 0.66 * ((hl2 - lo) / max(hi - lo, 0.001) - 0.5) + 0.67 * self._v
        v = 0.999 if v > 0.99 else -0.999 if v < -0.99 else v
        fish = 0.5 * math.log((1 + v) / (1 - v)) + 0.5 * self._fish
        trigger, self._v, self._fish = self._fish, v, fish
        return FisherValue(fish, trigger)
