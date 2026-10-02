"""Force index."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average import Ema


@indicator("force_index", "volume", inputs=("close", "volume"), warmup=lambda s: s.period + 1)
class ForceIndex(Indicator):
    """Elder force index: EMA(change(close) * volume, n), SMA-seeded; first value after ``period + 1`` bars."""

    def __init__(self, period: int = 13) -> None:
        self.period = _check(period)
        self._ema = Ema(period)
        self._prev: float | None = None

    def update(self, close: float, volume: float) -> float | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        return self._ema.update((close - prev) * volume)
