"""Rescaled-range Hurst exponent (statistical family)."""

from __future__ import annotations

import math
from collections import deque
from itertools import pairwise

from honba.strategies.indicators._base import Indicator, indicator


@indicator("hurst_exponent", "statistical", warmup=lambda s: s.length)
class HurstExponent(Indicator):
    """Single-window R/S Hurst estimate over the last ``length`` closes.

    Uses the n = length-1 price increments: R = max - min of the cumulative mean-adjusted
    sums, S = population stdev of increments, H = ln(R/S) / ln(n), clamped to [0, 1].
    Returns 0.5 (random walk) when S or R is 0. Simple estimator, biased for short windows.
    """

    def __init__(self, length: int = 100) -> None:
        if length < 8:
            raise ValueError(f"length must be >= 8, got {length}")
        self.length = length
        self._w: deque[float] = deque(maxlen=length)

    def update(self, close: float) -> float | None:
        self._w.append(close)
        if len(self._w) < self.length:
            return None
        w = list(self._w)
        d = [b - a for a, b in pairwise(w)]
        n = len(d)
        m = sum(d) / n
        s = math.sqrt(sum((v - m) ** 2 for v in d) / n)
        cum, lo, hi = 0.0, 0.0, 0.0
        for v in d:
            cum += v - m
            lo, hi = min(lo, cum), max(hi, cum)
        r = hi - lo
        if s <= 0 or r <= 0:
            return 0.5
        return max(0.0, min(1.0, math.log(r / s) / math.log(n)))
