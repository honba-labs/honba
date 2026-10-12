from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingSum
from honba.strategies.indicators._util import check as _check


@indicator(
    "ultimate_oscillator",
    "momentum",
    inputs=("high", "low", "close"),
    warmup=lambda s: max(s.fast, s.middle, s.slow) + 1,
)
class UltimateOscillator(Indicator):
    """Ultimate Oscillator (TradingView): 100 * (4*A_fast + 2*A_middle + A_slow) / 7, A_n = sum(BP, n) / sum(TR, n).

    BP = close - min(low, prev close); TR = max(high, prev close) - min(low, prev close).
    The first bar has no previous close and is skipped. A zero TR sum makes that average 0.
    """

    def __init__(self, fast: int = 7, middle: int = 14, slow: int = 28) -> None:
        self.fast, self.middle, self.slow = _check(fast), _check(middle), _check(slow)
        self._prev: float | None = None
        self._bp_fast, self._tr_fast = RollingSum(fast), RollingSum(fast)
        self._bp_middle, self._tr_middle = RollingSum(middle), RollingSum(middle)
        self._bp_slow, self._tr_slow = RollingSum(slow), RollingSum(slow)
        self._need = max(fast, middle, slow)
        self._fed = 0

    def update(self, high: float, low: float, close: float) -> float | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        lo, hi = min(low, prev), max(high, prev)
        bp, tr = close - lo, hi - lo
        bf = self._bp_fast.update(bp)
        tf = self._tr_fast.update(tr)
        bm = self._bp_middle.update(bp)
        tm = self._tr_middle.update(tr)
        bs = self._bp_slow.update(bp)
        ts = self._tr_slow.update(tr)
        self._fed += 1
        if self._fed < self._need:
            return None
        af = bf / tf if tf != 0 else 0.0
        am = bm / tm if tm != 0 else 0.0
        as_ = bs / ts if ts != 0 else 0.0
        return 100.0 * (4.0 * af + 2.0 * am + as_) / 7.0
