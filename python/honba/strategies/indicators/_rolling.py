"""O(1)-per-update rolling-window helpers shared by the indicator families."""

from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple

from honba.strategies.indicators._util import check as _check

_RECOMPUTE_MIN = 1000
_DROP = 1e-4  # recompute when m2 falls below this fraction of its peak since the last rebuild
_HUGE = 1e150  # |x| above this can overflow squares/sums (1e150**2 * n stays < 1.8e308)
_SHIFT_K2 = 1e3  # re-shift when (x - shift)**2 exceeds this multiple of the window variance
_CANCEL = 1e-4  # rebuild when a running total falls below this fraction of its running peak
_NAN = float("nan")


class RollingSum:
    """Sum over the last ``period`` values (``None`` until the window is full).

    A non-finite value in the window makes the sum NaN until it has left the window; the running
    total is then rebuilt exactly, so a NaN/inf never poisons later results. Overflow follows the
    ``RollingMoments`` convention: a value with ``|x| > 1e150`` is treated like a NaN/inf (the
    sum is reported as NaN while it is in the window, even if the exact sum is representable) and
    the total is rebuilt exactly, immediately, once the last such value has left. A running total
    that is nevertheless non-finite while the window is finite is rebuilt at once.

    The total is also rebuilt every ``max(1000, 4 * period)`` updates to bound rounding drift, and
    whenever it falls below ``1e-4`` of the largest magnitude it has had since the last rebuild
    (a big spike leaves rounding residue ~``spike * eps``, which would swamp a small true sum;
    the peak is tracked during warmup too). These guard rebuilds are rate-limited to one per
    ``max(1, period // 2)`` updates, so adversarial input cannot force ``O(period)`` work on
    more than ~``2 / period`` of the updates. This keeps the relative error near ``1e-10``.
    """

    def __init__(self, period: int) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self._mag = 0.0  # max |sum| since the last rebuild (warmup included)
        self._bad = 0  # unsafe (non-finite or huge) values currently in the window
        self._dirty = False  # running state not trustworthy; rebuild once the window is safe
        self._since = 0  # updates since the last rebuild
        self._every = max(_RECOMPUTE_MIN, 4 * period)
        self._gap = max(1, period // 2)  # minimum updates between cancellation-guard rebuilds

    def _rebuild(self) -> None:
        self._sum = math.fsum(self._w)
        self._mag = abs(self._sum)
        self._dirty = False
        self._since = 0

    def update(self, x: float) -> float | None:
        w = self._w
        full = len(w) == self.period
        if full and _unsafe(w[0]):
            self._bad -= 1
        if _unsafe(x):
            self._bad += 1
        old = w[0] if full else 0.0
        w.append(x)
        if self._bad:
            self._dirty = True
        elif self._dirty or self._since >= self._every:
            self._rebuild()
        else:
            self._sum += x - old if full else x
            self._since += 1
            a = abs(self._sum)
            if not math.isfinite(a) or (a < self._mag * _CANCEL and self._since >= self._gap):
                self._rebuild()
            elif a > self._mag:
                self._mag = a
        if len(w) < self.period:
            return None
        return _NAN if self._bad else self._sum


def _unsafe(v: float) -> bool:
    """True for NaN/inf and for magnitudes whose squares could overflow."""
    return not abs(v) <= _HUGE


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
    * when the newest value has drifted more than ``sqrt(1e3)`` (~32) window standard deviations
      from the shift (smooth trends: the shift is fixed at each rebuild, so ``x - shift`` would
      otherwise grow while ``m2`` stays small and relative precision would erode); the state is
      rebuilt around the current window mean. Measured on a 20000-bar random walk (rebuilds per
      update, period-dependent): ~7.6% at p=2, 2.2% at p=3, 0.83% at p=5, 0.25% at p=10 and
      0.18% at p>=20 (so <= 0.2% only for period >= ~20; the O(period) cost stays small for
      small periods); a ramp rebuilds once per ``~32 * std / slope`` updates;
    * when ``m2`` falls below ``1e-4`` of its peak since the last rebuild. A large outlier leaves
      a rounding residue of ~``outlier**2 * eps`` in ``m2``; once the outlier has left this
      residue would dominate the true (small) ``m2``, so the state is rebuilt exactly. Keeping
      ``m2 >= 1e-4 * peak`` bounds the residue's relative error at ~``1e-12``.

    Overflow: a value with ``|x| > 1e150`` (its square, summed, could exceed the float range) is
    treated like a NaN/inf: every moment (mean, variance, std) is reported as NaN while such a
    value is in the window, even when the exact result would have been representable, and the
    state is rebuilt exactly (from the window, with ``fsum``) once the last such value has left.
    A windowful of constant huge values is likewise NaN. Recovery is therefore immediate, not
    left to the periodic rebuild, and costs no ``O(period)`` work while the value is inside.

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
        if full and _unsafe(w[0]):
            self._bad -= 1
        if _unsafe(x):
            self._bad += 1
        self._run = self._run + 1 if w and w[-1] == x else 1
        if self._bad:
            self._dirty = True  # unsafe value in window: leave state alone, rebuild when clean
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
            if (
                self._since >= self._every
                or self._m2 < self._peak * _DROP
                or y * y > _SHIFT_K2 * self._m2 / n
                or not (math.isfinite(self._m2) and math.isfinite(self._mean))
            ):
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
