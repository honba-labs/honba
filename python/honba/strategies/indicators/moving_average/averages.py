"""Moving averages."""
from __future__ import annotations

import math
from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import (
    _CANCEL,
    _HUGE,
    _INF,
    _RECOMPUTE_MIN,
    _unsafe,
)
from honba.strategies.indicators._util import check as _check


@indicator("sma", "moving_average", warmup=lambda s: s.period)
class Sma(Indicator):
    def __init__(self, period: int = 5) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0

    def update(self, x: float) -> float | None:
        if len(self._w) == self.period:
            self._sum -= self._w[0]
        self._w.append(x)
        self._sum += x
        return self._sum / self.period if len(self._w) == self.period else None


@indicator("ema", "moving_average", warmup=lambda s: 1 if s.seed == "first" else s.period)
class Ema(Indicator):
    """EMA with alpha = 2/(period+1).

    ``seed="sma"`` (TA-Lib) starts from the SMA of the first ``period`` values;
    ``seed="first"`` (Jesse) starts from the first value, so it is valid from bar 0.
    """

    def __init__(self, period: int = 5, seed: str = "sma") -> None:
        self.period = _check(period)
        if seed not in ("sma", "first"):
            raise ValueError(f"seed must be 'sma' or 'first', got {seed!r}")
        self.seed = seed
        self._alpha = 2.0 / (period + 1)
        self._seed = Sma(period) if seed == "sma" else None
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.value = self._seed.update(x) if self._seed else x
        else:
            self.value = x * self._alpha + self.value * (1 - self._alpha)
        return self.value


@indicator("rma", "moving_average", warmup=lambda s: s.period)
class Rma(Indicator):
    """Wilder's smoothing: SMA seed, then ``(prev * (n - 1) + x) / n``."""

    def __init__(self, period: int = 14) -> None:
        self.period = _check(period)
        self._seed = Sma(period)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.value = self._seed.update(x)
        else:
            self.value = (self.value * (self.period - 1) + x) / self.period
        return self.value


@indicator("wma", "moving_average", warmup=lambda s: s.period)
class Wma(Indicator):
    """Linearly weighted moving average (newest value has the largest weight).

    O(1) per update. A non-finite value in the window makes the result NaN until it has left
    the window (running state untouched meanwhile, then rebuilt exactly). Overflow follows the
    ``RollingMoments`` convention: a value with ``|x| > 1e150`` is treated like a NaN/inf (result
    NaN while it is in the window, exact rebuild immediately once the last one has left); a
    non-finite numerator with a safe window is rebuilt at once. The state is also rebuilt every
    ``max(1000, 4 * period)`` updates to bound rounding drift, and whenever the weighted numerator
    falls below ``1e-4`` of the largest magnitude it has had since the last rebuild (tracked
    during warmup too; cancellation after a large spike leaves residue that would swamp a small
    result). Guard rebuilds are rate-limited to one per ``max(1, period // 2)`` updates.
    """

    def __init__(self, period: int = 5) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0  # S: plain window sum
        self._mag = 0.0  # max |N| since the last rebuild (warmup included)
        self._num = 0.0  # N: sum of weight_i * value_i, weights 1..n oldest to newest
        self._den = period * (period + 1) / 2
        self._bad = 0  # unsafe (non-finite or huge) values currently in the window
        self._dirty = False
        self._ok = False  # window full, all values safe, state clean (fast path allowed)
        self._since = 0
        self._every = max(_RECOMPUTE_MIN, 4 * period)
        self._gap = max(1, period // 2)  # minimum updates between cancellation-guard rebuilds

    def _recompute(self) -> None:
        self._sum = math.fsum(self._w)
        self._num = math.fsum(k * v for k, v in enumerate(self._w, 1))
        self._mag = abs(self._num)
        self._since = 0
        self._dirty = False

    def update(self, x: float) -> float | None:
        n, w = self.period, self._w
        if self._ok and len(w) == n and -_HUGE <= x <= _HUGE:
            # steady state: full, finite, clean. The window slides: every weight drops by one
            # (N -= S), old leaves, x enters at n.
            old = w[0]
            w.append(x)
            s = self._sum
            num = self._num + (n * x - s)
            self._sum = s + (x - old)
            t = self._since + 1
            self._since = t
            a = num if num >= 0.0 else -num
            mag = self._mag
            if t >= self._every or not (a < _INF) or (a < mag * _CANCEL and t >= self._gap):
                self._num = num
                self._recompute()
                num = self._num
            else:
                self._num = num
                if a > mag:
                    self._mag = a
            return num / self._den
        return self._slow(x)

    def _slow(self, x: float) -> float | None:
        n, w = self.period, self._w
        full = len(w) == n
        if full and _unsafe(w[0]):
            self._bad -= 1
        if _unsafe(x):
            self._bad += 1
        w.append(x)
        if self._bad:
            self._dirty = True
            self._ok = False
        elif self._dirty or self._since >= self._every:
            self._recompute()
            self._ok = len(w) == n
        else:
            self._sum += x
            self._num += len(w) * x
            self._since += 1
            a = abs(self._num)
            if not math.isfinite(a) or (a < self._mag * _CANCEL and self._since >= self._gap):
                self._recompute()
            elif a > self._mag:
                self._mag = a
            self._ok = len(w) == n
        if len(w) < n:
            return None
        return math.nan if self._bad else self._num / self._den


_MA_KINDS = {"sma": Sma, "ema": Ema, "rma": Rma, "wma": Wma}


def make_ma(kind: str, period: int):
    """Moving-average factory: ``kind`` is one of sma, ema, rma (Wilder), wma."""
    try:
        return _MA_KINDS[kind](period)
    except KeyError:
        raise ValueError(
            f"unknown moving average {kind!r}; choose from {sorted(_MA_KINDS)}"
        ) from None
