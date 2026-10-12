"""Chaikin money flow."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingSum
from honba.strategies.indicators._util import check as _check

from .accumulation_distribution import clv_volume


@indicator(
    "chaikin_money_flow",
    "volume",
    inputs=("high", "low", "close", "volume"),
    warmup=lambda s: s.period,
)
class ChaikinMoneyFlow(Indicator):
    """Chaikin money flow: sum(CLV*volume, n) / sum(volume, n); 0 when the window volume is 0."""

    def __init__(self, period: int = 20) -> None:
        self.period = _check(period)
        self._mf = RollingSum(period)
        self._vol = RollingSum(period)

    def update(self, high: float, low: float, close: float, volume: float) -> float | None:
        mf = self._mf.update(clv_volume(high, low, close, volume))
        vol = self._vol.update(volume)
        if mf is None:
            return None
        return mf / vol if vol else 0.0
