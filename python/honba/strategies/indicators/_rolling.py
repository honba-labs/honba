"""O(1)-per-update rolling-window helpers shared by the indicator families."""

from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple

from honba.strategies.indicators._util import check as _check

_RECOMPUTE_MIN = 1000


class RollingSum:
    """Sum over the last ``period`` values (``None`` until the window is full)."""

    def __init__(self, period: int) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0

    def update(self, x: float) -> float | None:
        if len(self._w) == self.period:
            self._sum -= self._w[0]
        self._w.append(x)
        self._sum += x
        return self._sum if len(self._w) == self.period else None


class Moments(NamedTuple):
    mean: float
    variance: float
    std: float


class RollingMoments:
    """Mean / variance / std over the last ``period`` values, O(1) per update.

    Uses Welford's algorithm with removal (replace-oldest form), which does not suffer the
    catastrophic cancellation of a raw sum / sum-of-squares at high price levels. Rounding drift
    is bounded by an exact recompute every ``max(1000, 4 * period)`` updates (amortised O(1)).
    A window of identical values reports variance exactly 0. ``ddof`` follows numpy (0 = population).
    """

    def __init__(self, period: int, ddof: int = 0) -> None:
        self.period = _check(period)
        if ddof < 0 or period - ddof < 1:
            raise ValueError(
                f"ddof must satisfy 0 <= ddof < period, got ddof={ddof}, period={period}"
            )
        self.ddof = ddof
        self._w: deque[float] = deque(maxlen=period)
        self._shift = 0.0
        self._mean = 0.0  # mean of (value - shift)
        self._m2 = 0.0
        self._run = 0  # consecutive identical trailing values
        self._since = 0
        self._every = max(_RECOMPUTE_MIN, 4 * period)

    def _recompute(self) -> None:
        n = len(self._w)
        self._shift = math.fsum(self._w) / n
        sh = self._shift
        self._mean = math.fsum(v - sh for v in self._w) / n  # ~0, kept for exactness
        m = self._mean
        self._m2 = math.fsum((v - sh - m) ** 2 for v in self._w)
        self._since = 0

    def update(self, x: float) -> Moments | None:
        w, n = self._w, self.period
        if not w:
            self._shift = x
        self._run = self._run + 1 if w and w[-1] == x else 1
        y = x - self._shift  # Welford runs on shifted values so it never sees the price level
        if len(w) == n:
            old = w[0] - self._shift
            w.append(x)  # evicts old
            delta = y - old
            new_mean = self._mean + delta / n
            self._m2 += delta * ((y - new_mean) + (old - self._mean))
            self._mean = new_mean
            self._since += 1
            if self._since >= self._every:
                self._recompute()
        else:
            w.append(x)
            k = len(w)
            d = y - self._mean
            self._mean += d / k
            self._m2 += d * (y - self._mean)
            if k < n:
                return None
        if self._run >= n:
            self._shift, self._mean, self._m2 = x, 0.0, 0.0
        var = self._m2 / (n - self.ddof)
        var = max(var, 0.0)
        return Moments(self._shift + self._mean, var, math.sqrt(var))
