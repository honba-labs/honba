"""Moving averages."""
from __future__ import annotations

from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
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
    """Linearly weighted moving average (newest value has the largest weight)."""

    def __init__(self, period: int = 5) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0  # S: plain window sum
        self._num = 0.0  # N: sum of weight_i * value_i, weights 1..n oldest to newest
        self._den = period * (period + 1) / 2

    def update(self, x: float) -> float | None:
        n = self.period
        if len(self._w) == n:
            # Window slides: every weight drops by one (N -= S), old value leaves, x enters at n.
            self._num += n * x - self._sum
            self._sum += x - self._w[0]
        else:
            self._sum += x
            self._num += (len(self._w) + 1) * x
        self._w.append(x)
        return self._num / self._den if len(self._w) == n else None


_MA_KINDS = {"sma": Sma, "ema": Ema, "rma": Rma, "wma": Wma}


def make_ma(kind: str, period: int):
    """Moving-average factory: ``kind`` is one of sma, ema, rma (Wilder), wma."""
    try:
        return _MA_KINDS[kind](period)
    except KeyError:
        raise ValueError(f"unknown moving average {kind!r}; choose from {sorted(_MA_KINDS)}") from None
