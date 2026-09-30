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
    def __init__(self, period: int) -> None:
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
    """EMA seeded with the SMA of the first ``period`` values, alpha = 2/(period+1)."""

    def __init__(self, period: int) -> None:
        self.period = _check(period)
        self._alpha = 2.0 / (period + 1)
        self._seed = Sma(period)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.value = self._seed.update(x)
        else:
            self.value = x * self._alpha + self.value * (1 - self._alpha)
        return self.value


class Rsi:
    """Wilder RSI; first value after ``period + 1`` inputs."""

    def __init__(self, period: int) -> None:
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
            self.value = 100.0 if self._avg_gain > 0 else 50.0
        else:
            self.value = 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)
        return self.value


@dataclass(frozen=True, slots=True)
class MacdValue:
    macd: float
    signal: float
    histogram: float


class Macd:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self._fast, self._slow, self._signal = Ema(fast), Ema(slow), Ema(signal)

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
    """Bands at ``mult`` population standard deviations around the SMA."""

    def __init__(self, period: int = 20, mult: float = 2.0) -> None:
        self.period, self.mult = _check(period), mult
        self._w: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> BollingerValue | None:
        self._w.append(x)
        if len(self._w) < self.period:
            return None
        mean = sum(self._w) / self.period
        sd = math.sqrt(sum((v - mean) ** 2 for v in self._w) / self.period)
        return BollingerValue(mean + self.mult * sd, mean, mean - self.mult * sd)


@dataclass(frozen=True, slots=True)
class DonchianValue:
    upper: float
    lower: float


class Donchian:
    """Highest high / lowest low over the last ``period`` bars, including the current one."""

    def __init__(self, period: int) -> None:
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
    """Wilder ATR; first value after ``period`` true ranges (``period + 1`` bars)."""

    def __init__(self, period: int) -> None:
        self.period = _check(period)
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        prev, self._prev_close = self._prev_close, close
        if prev is None:
            return None
        tr = max(high - low, abs(high - prev), abs(low - prev))
        if self.value is not None:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        else:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs) / self.period
        return self.value


class Kdj:
    """Stochastic KDJ: RSV over ``fastk`` bars, K = SMA(RSV), D = SMA(K), J = 3K - 2D.

    RSV is 0 on a flat window (talib convention). ``update`` returns ``(k, d, j)``
    or ``None`` until warm.
    """

    def __init__(self, fastk: int = 9, slowk: int = 3, slowd: int = 3) -> None:
        self._h: deque[float] = deque(maxlen=_check(fastk))
        self._l: deque[float] = deque(maxlen=fastk)
        self._rsv: deque[float] = deque(maxlen=_check(slowk))
        self._k: deque[float] = deque(maxlen=_check(slowd))

    def update(self, high: float, low: float, close: float) -> tuple[float, float, float] | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self._h.maxlen:
            return None
        hh, ll = max(self._h), min(self._l)
        self._rsv.append((close - ll) / (hh - ll) * 100 if hh != ll else 0.0)
        if len(self._rsv) < self._rsv.maxlen:
            return None
        self._k.append(sum(self._rsv) / len(self._rsv))
        if len(self._k) < self._k.maxlen:
            return None
        k, d = self._k[-1], sum(self._k) / len(self._k)
        return k, d, 3 * k - 2 * d


class Ichimoku:
    """Ichimoku cloud as plotted on the current bar: the span values computed
    ``displacement`` bars ago. ``update`` returns ``(span_a, span_b)`` or ``None`` until warm."""

    def __init__(
        self, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26
    ) -> None:
        self._n = (_check(tenkan), _check(kijun), _check(senkou_b))
        self._h: deque[float] = deque(maxlen=max(self._n))
        self._l: deque[float] = deque(maxlen=max(self._n))
        self._spans: deque[tuple[float, float]] = deque(maxlen=displacement + 1)

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


__all__ = [
    "Sma", "Ema", "Rsi", "Macd", "MacdValue", "Bollinger", "BollingerValue",
    "Donchian", "DonchianValue", "Atr", "Kdj", "Ichimoku",
]
