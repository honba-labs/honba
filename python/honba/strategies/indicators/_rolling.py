"""O(1)-per-update rolling-window helpers shared by the indicator families."""

from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple

from honba.strategies.indicators._util import check as _check

_RECOMPUTE_MIN = 1000
_DROP = 1e-4  # recompute when m2 falls below this fraction of its peak since the last rebuild
_NAN = float("nan")


class RollingSum:
    """Sum over the last ``period`` values (``None`` until the window is full).

    A non-finite value in the window makes the sum NaN until it has left the window; the running
    total is then rebuilt exactly, so a NaN/inf never poisons later results. The total is also
    rebuilt every ``max(1000, 4 * period)`` updates to bound rounding drift.
    """

    def __init__(self, period: int) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self._bad = 0  # non-finite values currently in the window
        self._dirty = False  # running state not trustworthy; rebuild once the window is finite
        self._since = 0
        self._every = max(_RECOMPUTE_MIN, 4 * period)

    def update(self, x: float) -> float | None:
        w = self._w
        if len(w) == self.period and not math.isfinite(w[0]):
            self._bad -= 1
        if not math.isfinite(x):
            self._bad += 1
        full = len(w) == self.period
        old = w[0] if full else 0.0
        w.append(x)
        if self._bad:
            self._dirty = True
        elif self._dirty or self._since >= self._every:
            self._sum = math.fsum(w)
            self._dirty = False
            self._since = 0
        else:
            self._sum += x - old if full else x
            self._since += 1
        if len(w) < self.period:
            return None
        return _NAN if self._bad else self._sum


class Moments(NamedTuple):
    mean: float
    variance: float
    std: float


class RollingMoments:
    """Mean / variance / std over the last ``period`` values, O(1) amortised per update.

    Uses Welford's algorithm with removal (replace-oldest form), which does not suffer the
    catastrophic cancellation of a raw sum / sum-of-squares at high price levels. Exact rebuilds
    (O(period)) happen in three cases, all amortised O(1) on ordinary data:

    * every ``max(1000, 4 * period)`` updates (bounds ordinary rounding drift);
    * when the last non-finite value leaves the window. While a NaN/inf is in the window all
      moments are NaN and the running state is left untouched, so it cannot be poisoned;
    * when ``m2`` falls below ``1e-4`` of its peak since the last rebuild. A large outlier leaves
      a rounding residue of ~``outlier**2 * eps`` in ``m2``; once the outlier has left this
      residue would dominate the true (small) ``m2``, so the state is rebuilt exactly. Keeping
      ``m2 >= 1e-4 * peak`` bounds the residue's relative error at ~``1e-12``.

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
        self._peak = 0.0  # max m2 since the last rebuild
        self._run = 0  # consecutive identical trailing values
        self._bad = 0  # non-finite values currently in the window
        self._dirty = False  # state not trustworthy; rebuild once the window is finite
        self._since = 0
        self._every = max(_RECOMPUTE_MIN, 4 * period)

    def _recompute(self) -> None:
        n = len(self._w)
        self._shift = math.fsum(self._w) / n
        sh = self._shift
        self._mean = math.fsum(v - sh for v in self._w) / n  # ~0, kept for exactness
        m = self._mean
        self._m2 = math.fsum((v - sh - m) ** 2 for v in self._w)
        self._peak = self._m2
        self._since = 0
        self._dirty = False

    def update(self, x: float) -> Moments | None:
        w, n = self._w, self.period
        full = len(w) == n
        if full and not math.isfinite(w[0]):
            self._bad -= 1
        if not math.isfinite(x):
            self._bad += 1
        self._run = self._run + 1 if w and w[-1] == x else 1
        if self._bad:
            self._dirty = True  # leave running state alone; it is rebuilt when the window is clean
            w.append(x)
            return _NAN_MOMENTS if len(w) == n else None
        if self._dirty:
            w.append(x)
            self._recompute()
            if len(w) < n:
                return None
        elif full:
            old = w[0] - self._shift
            w.append(x)  # evicts old
            y = x - self._shift  # Welford runs on shifted values so it never sees the price level
            delta = y - old
            new_mean = self._mean + delta / n
            self._m2 += delta * ((y - new_mean) + (old - self._mean))
            self._mean = new_mean
            self._since += 1
            self._peak = max(self._peak, self._m2)
            if self._since >= self._every or self._m2 < self._peak * _DROP:
                self._recompute()
        else:
            if not w:
                self._shift = x
            w.append(x)
            k = len(w)
            y = x - self._shift
            d = y - self._mean
            self._mean += d / k
            self._m2 += d * (y - self._mean)
            if k < n:
                return None
            self._peak = self._m2
        if self._run >= n:
            self._shift, self._mean, self._m2, self._peak = x, 0.0, 0.0, 0.0
        var = self._m2 / (n - self.ddof)
        var = max(var, 0.0)
        return Moments(self._shift + self._mean, var, math.sqrt(var))


_NAN_MOMENTS = Moments(_NAN, _NAN, _NAN)
