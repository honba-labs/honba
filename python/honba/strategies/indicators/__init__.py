"""Streaming indicators. ``update`` returns ``None`` until warmed up."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


def _check(period: int) -> int:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    return period


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


class Rsi:
    """Wilder RSI; first value after ``period + 1`` inputs. A window with no losses is 100."""

    def __init__(self, period: int = 14) -> None:
        self.period = _check(period)
        self._prev: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain = self._avg_loss = 0.0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self._prev is None:
            self._prev = x
            return None
        change, self._prev = x - self._prev, x
        gain, loss = max(change, 0.0), max(-change, 0.0)
        n = self.period
        if len(self._gains) < n:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < n:
                return None
            self._avg_gain, self._avg_loss = sum(self._gains) / n, sum(self._losses) / n
        else:
            self._avg_gain = (self._avg_gain * (n - 1) + gain) / n
            self._avg_loss = (self._avg_loss * (n - 1) + loss) / n
        if self._avg_loss == 0:
            self.value = 100.0
        else:
            self.value = 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)
        return self.value


@dataclass(frozen=True, slots=True)
class MacdValue:
    macd: float
    signal: float
    histogram: float


class Macd:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9, seed: str = "sma") -> None:
        if not 0 < _check(fast) < _check(slow):
            raise ValueError(f"fast must be less than slow, got {fast} and {slow}")
        self._fast, self._slow, self._signal = Ema(fast, seed), Ema(slow, seed), Ema(_check(signal), seed)

    def update(self, x: float) -> MacdValue | None:
        f, s = self._fast.update(x), self._slow.update(x)
        if f is None or s is None:
            return None
        line = f - s
        sig = self._signal.update(line)
        if sig is None:
            return None
        return MacdValue(line, sig, line - sig)


@dataclass(frozen=True, slots=True)
class BollingerValue:
    upper: float
    middle: float
    lower: float


class Bollinger:
    """Bands at ``mult`` (upper) and ``mult_lower`` (default ``mult``) population
    standard deviations around the SMA."""

    def __init__(self, period: int = 20, mult: float = 2.0, mult_lower: float | None = None) -> None:
        self.period, self.mult = _check(period), mult
        self.mult_lower = mult if mult_lower is None else mult_lower
        if mult < 0 or self.mult_lower < 0:
            raise ValueError("band multipliers must be >= 0")
        self._w: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> BollingerValue | None:
        self._w.append(x)
        if len(self._w) < self.period:
            return None
        mean = sum(self._w) / self.period
        sd = math.sqrt(sum((v - mean) ** 2 for v in self._w) / self.period)
        return BollingerValue(mean + self.mult * sd, mean, mean - self.mult_lower * sd)


@dataclass(frozen=True, slots=True)
class DonchianValue:
    upper: float
    lower: float


class Donchian:
    """Highest high / lowest low over the last ``period`` bars, including the current one."""

    def __init__(self, period: int = 20) -> None:
        self.period = _check(period)
        self._h: deque[float] = deque(maxlen=period)
        self._l: deque[float] = deque(maxlen=period)

    def update(self, high: float, low: float) -> DonchianValue | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self.period:
            return None
        return DonchianValue(max(self._h), min(self._l))


class Atr:
    """Wilder ATR. By default the first true range needs a previous close, so the first
    value appears after ``period + 1`` bars; ``include_first_bar=True`` (Jesse) uses
    high - low for the first bar, giving the first value after ``period`` bars."""

    def __init__(self, period: int = 14, include_first_bar: bool = False) -> None:
        self.period = _check(period)
        self.include_first_bar = include_first_bar
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        prev, self._prev_close = self._prev_close, close
        if prev is None:
            if not self.include_first_bar:
                return None
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev), abs(low - prev))
        if self.value is not None:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        else:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs) / self.period
        return self.value


class Kdj:
    """Stochastic KDJ: RSV over ``fastk`` bars, K = MA(RSV), D = MA(K), J = 3K - 2D.

    ``slowk_ma`` / ``slowd_ma`` choose the smoothing (see :func:`make_ma`; default SMA).
    RSV is 0 on a flat window (talib convention). ``update`` returns ``(k, d, j)``
    or ``None`` until warm.
    """

    def __init__(
        self, fastk: int = 9, slowk: int = 3, slowd: int = 3,
        slowk_ma: str = "sma", slowd_ma: str = "sma",
    ) -> None:
        self._h: deque[float] = deque(maxlen=_check(fastk))
        self._l: deque[float] = deque(maxlen=fastk)
        self._k = make_ma(slowk_ma, _check(slowk))
        self._d = make_ma(slowd_ma, _check(slowd))

    def update(self, high: float, low: float, close: float) -> tuple[float, float, float] | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self._h.maxlen:
            return None
        hh, ll = max(self._h), min(self._l)
        k = self._k.update((close - ll) / (hh - ll) * 100 if hh != ll else 0.0)
        d = self._d.update(k) if k is not None else None
        if k is None or d is None:
            return None
        return k, d, 3 * k - 2 * d


class Ichimoku:
    """Ichimoku cloud as plotted on the current bar: spans computed ``displacement - 1``
    bars earlier (the standard convention, matching Jesse). ``update`` returns
    ``(span_a, span_b)`` or ``None`` until warm."""

    def __init__(
        self, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26
    ) -> None:
        self._n = (_check(tenkan), _check(kijun), _check(senkou_b))
        self._h: deque[float] = deque(maxlen=max(self._n))
        self._l: deque[float] = deque(maxlen=max(self._n))
        self._spans: deque[tuple[float, float]] = deque(maxlen=_check(displacement))

    def _mid(self, n: int) -> float:
        return (max(list(self._h)[-n:]) + min(list(self._l)[-n:])) / 2

    def update(self, high: float, low: float) -> tuple[float, float] | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self._h.maxlen:
            return None
        tenkan, kijun, senkou_b = (self._mid(n) for n in self._n)
        self._spans.append(((tenkan + kijun) / 2, senkou_b))
        return self._spans[0] if len(self._spans) == self._spans.maxlen else None


_KINDS = {
    "sma": Sma, "ema": Ema, "rma": Rma, "wma": Wma, "rsi": Rsi, "atr": Atr, "macd": Macd,
    "bollinger": Bollinger, "donchian": Donchian, "ichimoku": Ichimoku, "kdj": Kdj,
}


def build_indicator(kind: str, **params):
    """Builds an indicator from a config-style spec, e.g. ``build_indicator("ema", period=20)``.

    Unknown kinds raise ``ValueError``; unknown or invalid parameters raise ``TypeError`` /
    ``ValueError`` from the indicator itself.
    """
    try:
        cls = _KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown indicator {kind!r}; choose from {sorted(_KINDS)}") from None
    return cls(**params)


__all__ = [
    "Sma", "Ema", "Rma", "Wma", "Rsi", "Macd", "MacdValue", "Bollinger", "BollingerValue",
    "Donchian", "DonchianValue", "Atr", "Kdj", "Ichimoku", "make_ma", "build_indicator",
]
