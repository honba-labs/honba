"""Williams fractals (support_resistance family)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator


@dataclass(frozen=True, slots=True)
class WilliamsFractalsValue:
    up: float
    down: float


@indicator(
    "williams_fractals",
    "support_resistance",
    inputs=("high", "low"),
    outputs=("up", "down"),
    warmup=lambda s: 5,
)
class WilliamsFractals(Indicator):
    """5-bar Williams fractals, confirmed two bars late; flags 1.0/0.0 refer to bar t-2.

    up = 1.0 if high[t-2] is strictly greater than the two highs on each side;
    down = 1.0 if low[t-2] is strictly lower than the two lows on each side.
    """

    def __init__(self) -> None:
        self._h: deque[float] = deque(maxlen=5)
        self._l: deque[float] = deque(maxlen=5)

    def update(self, high: float, low: float) -> WilliamsFractalsValue | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < 5:
            return None
        h, l = self._h, self._l
        up = all(h[2] > h[i] for i in (0, 1, 3, 4))
        down = all(l[2] < l[i] for i in (0, 1, 3, 4))
        return WilliamsFractalsValue(1.0 if up else 0.0, 1.0 if down else 0.0)
