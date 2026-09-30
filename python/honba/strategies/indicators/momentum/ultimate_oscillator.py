from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check

@indicator("ultimate_oscillator", "momentum", inputs=("high", "low", "close"),
           warmup=lambda s: max(s.fast, s.middle, s.slow) + 1)
class UltimateOscillator(Indicator):
    """Ultimate Oscillator (TradingView): 100 * (4*A_fast + 2*A_middle + A_slow) / 7, A_n = sum(BP, n) / sum(TR, n).

    BP = close - min(low, prev close); TR = max(high, prev close) - min(low, prev close).
    The first bar has no previous close and is skipped. A zero TR sum makes that average 0.
    """

    def __init__(self, fast: int = 7, middle: int = 14, slow: int = 28) -> None:
        self.fast, self.middle, self.slow = _check(fast), _check(middle), _check(slow)
        self._prev: float | None = None
        n = max(fast, middle, slow)
        self._bp: deque[float] = deque(maxlen=n)
        self._tr: deque[float] = deque(maxlen=n)

    def _avg(self, n: int) -> float:
        tr = sum(list(self._tr)[-n:])
        return sum(list(self._bp)[-n:]) / tr if tr != 0 else 0.0

    def update(self, high: float, low: float, close: float) -> float | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        lo, hi = min(low, prev), max(high, prev)
        self._bp.append(close - lo)
        self._tr.append(hi - lo)
        if len(self._tr) < self._tr.maxlen:
            return None
        return 100.0 * (4 * self._avg(self.fast) + 2 * self._avg(self.middle) + self._avg(self.slow)) / 7.0
