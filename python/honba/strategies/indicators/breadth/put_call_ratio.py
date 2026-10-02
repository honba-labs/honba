"""Put/Call ratio (breadth family)."""

from __future__ import annotations

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average import Sma
from honba.strategies.indicators._base import Indicator, indicator


@indicator(
    "put_call_ratio", "breadth", inputs=("put_volume", "call_volume"), warmup=lambda s: s.smoothing
)
class PutCallRatio(Indicator):
    """SMA(smoothing) of the per-bar put_volume / call_volume; smoothing=1 is the raw ratio.

    Convention: bars with call_volume <= 0 count as neutral 1.0.
    """

    def __init__(self, smoothing: int = 1) -> None:
        self.smoothing = _check(smoothing)
        self._sma = Sma(smoothing)

    def update(self, put_volume: float, call_volume: float) -> float | None:
        return self._sma.update(put_volume / call_volume if call_volume > 0 else 1.0)
