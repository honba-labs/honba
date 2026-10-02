"""Rolling Fibonacci retracement levels (support_resistance family)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class FibonacciRetracementValue:
    l0: float
    l236: float
    l382: float
    l500: float
    l618: float
    l786: float
    l1000: float


_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


@indicator(
    "fibonacci_retracement",
    "support_resistance",
    inputs=("high", "low"),
    outputs=("l0", "l236", "l382", "l500", "l618", "l786", "l1000"),
    warmup=lambda s: s.length,
)
class FibonacciRetracement(Indicator):
    """Fibonacci levels of the rolling ``length``-bar range: level = highest high - range * ratio.

    l0 is the highest high, l1000 the lowest low (window includes the current bar).
    """

    def __init__(self, length: int = 100) -> None:
        self.length = _check(length)
        self._h: deque[float] = deque(maxlen=length)
        self._l: deque[float] = deque(maxlen=length)

    def update(self, high: float, low: float) -> FibonacciRetracementValue | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self.length:
            return None
        hi, lo = max(self._h), min(self._l)
        rng = hi - lo
        return FibonacciRetracementValue(*(hi - rng * r for r in _RATIOS))
