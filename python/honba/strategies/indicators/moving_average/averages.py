"""Moving averages."""
from __future__ import annotations

from collections import deque

from honba.strategies.indicators._util import check as _check


class Sma:
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


class Ema:
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


class Rma:
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


class Wma:
    """Linearly weighted moving average (newest value has the largest weight)."""

    def __init__(self, period: int = 5) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> float | None:
        self._w.append(x)
        if len(self._w) < self.period:
            return None
        n = self.period
        return sum(w * v for w, v in enumerate(self._w, 1)) / (n * (n + 1) / 2)


_MA_KINDS = {"sma": Sma, "ema": Ema, "rma": Rma, "wma": Wma}


def make_ma(kind: str, period: int):
    """Moving-average factory: ``kind`` is one of sma, ema, rma (Wilder), wma."""
    try:
        return _MA_KINDS[kind](period)
    except KeyError:
        raise ValueError(f"unknown moving average {kind!r}; choose from {sorted(_MA_KINDS)}") from None
