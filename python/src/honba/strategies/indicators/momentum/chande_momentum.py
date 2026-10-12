from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingSum
from honba.strategies.indicators._util import check as _check


@indicator("cmo", "momentum", warmup=lambda s: s.length + 1)
class ChandeMomentum(Indicator):
    """Chande Momentum Oscillator (TradingView): 100 * (sum of gains - sum of losses) / (sum of gains + sum of losses) over ``length`` changes.

    Simple sums (no Wilder smoothing); range -100..100. No movement at all gives 0.
    """

    def __init__(self, length: int = 9) -> None:
        self.length = _check(length)
        self._prev: float | None = None
        self._up = RollingSum(length)
        self._dn = RollingSum(length)

    def update(self, close: float) -> float | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        d = close - prev
        up = self._up.update(d if d > 0 else 0.0)
        dn = self._dn.update(-d if d < 0 else 0.0)
        if up is None or dn is None:
            return None
        return 100.0 * (up - dn) / (up + dn) if up + dn != 0 else 0.0
