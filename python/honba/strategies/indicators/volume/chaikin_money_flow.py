"""Chaikin money flow."""
from __future__ import annotations

from collections import deque

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator
from .accumulation_distribution import clv_volume


@indicator("chaikin_money_flow", "volume", inputs=("high", "low", "close", "volume"), warmup=lambda s: s.period)
class ChaikinMoneyFlow(Indicator):
    """Chaikin money flow: sum(CLV*volume, n) / sum(volume, n); 0 when the window volume is 0."""

    def __init__(self, period: int = 20) -> None:
        self.period = _check(period)
        self._mf: deque[float] = deque(maxlen=period)
        self._vol: deque[float] = deque(maxlen=period)

    def update(self, high: float, low: float, close: float, volume: float) -> float | None:
        self._mf.append(clv_volume(high, low, close, volume))
        self._vol.append(volume)
        if len(self._mf) < self.period:
            return None
        sv = sum(self._vol)
        return sum(self._mf) / sv if sv else 0.0
