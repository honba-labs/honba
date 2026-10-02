from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

from honba.strategies.indicators.moving_average import Sma


@dataclass(slots=True)
class RvgiValue:
    rvgi: float
    signal: float


def _swma(a: float, b: float, c: float, d: float) -> float:
    return (a + 2 * b + 2 * c + d) / 6.0


@indicator(
    "relative_vigor_index",
    "momentum",
    inputs=("open", "high", "low", "close"),
    outputs=("rvgi", "signal"),
    warmup=lambda s: s.length + 6,
)
class RelativeVigorIndex(Indicator):
    """Relative Vigor Index (TradingView RVGI): SMA(SWMA(close-open), n) / SMA(SWMA(high-low), n); signal = SWMA(rvgi).

    SWMA weights the last four values 1,2,2,1 (/6, newest last). A zero denominator gives 0.
    """

    def __init__(self, length: int = 10) -> None:
        self.length = _check(length)
        self._co: deque[float] = deque(maxlen=4)
        self._hl: deque[float] = deque(maxlen=4)
        self._num, self._den = Sma(length), Sma(length)
        self._r: deque[float] = deque(maxlen=4)

    def update(self, open: float, high: float, low: float, close: float) -> RvgiValue | None:
        self._co.append(close - open)
        self._hl.append(high - low)
        if len(self._co) < 4:
            return None
        n = self._num.update(_swma(*self._co))
        d = self._den.update(_swma(*self._hl))
        if n is None:
            return None
        self._r.append(n / d if d != 0 else 0.0)
        if len(self._r) < 4:
            return None
        return RvgiValue(self._r[-1], _swma(*self._r))
