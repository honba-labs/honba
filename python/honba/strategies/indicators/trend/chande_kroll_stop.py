"""Chande Kroll stop."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.volatility.atr import Atr


@dataclass(frozen=True, slots=True)
class ChandeKrollStopValue:
    long_stop: float
    short_stop: float


@indicator("chande_kroll_stop", "trend", inputs=("high", "low", "close"), outputs=("long_stop", "short_stop"),
           warmup=lambda s: s.p + s.q - 1)
class ChandeKrollStop(Indicator):
    """Chande Kroll stop: long_stop = highest(highest(high,p) - x*ATR(p), q);
    short_stop = lowest(lowest(low,p) + x*ATR(p), q). ATR is Wilder (first TR = high-low).

    Note TradingView labels these two series the other way round ("stop short" is the higher one).
    """

    def __init__(self, p: int = 10, x: float = 1.0, q: int = 9) -> None:
        self.p, self.q = _check(p), _check(q)
        if x <= 0:
            raise ValueError(f"x must be > 0, got {x}")
        self.x = float(x)
        self._atr = Atr(p, include_first_bar=True)
        self._h: deque[float] = deque(maxlen=p)
        self._l: deque[float] = deque(maxlen=p)
        self._hs: deque[float] = deque(maxlen=q)
        self._ls: deque[float] = deque(maxlen=q)

    def update(self, high: float, low: float, close: float) -> ChandeKrollStopValue | None:
        self._h.append(high)
        self._l.append(low)
        atr = self._atr.update(high, low, close)
        if atr is None:
            return None
        self._hs.append(max(self._h) - self.x * atr)
        self._ls.append(min(self._l) + self.x * atr)
        if len(self._hs) < self.q:
            return None
        return ChandeKrollStopValue(max(self._hs), min(self._ls))
