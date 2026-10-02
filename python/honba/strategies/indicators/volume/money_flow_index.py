"""Money flow index."""

from __future__ import annotations

from collections import deque

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator


@indicator(
    "money_flow_index",
    "volume",
    inputs=("high", "low", "close", "volume"),
    warmup=lambda s: s.period + 1,
)
class MoneyFlowIndex(Indicator):
    """Money flow index (TradingView): raw flow = hlc3*volume, split into positive/negative by the
    hlc3 change; 100 - 100/(1+pos/neg) over ``period`` bars. Needs a previous bar, so the first value
    appears after ``period + 1`` bars. Convention: no flow at all -> 50; only positive flow -> 100."""

    def __init__(self, period: int = 14) -> None:
        self.period = _check(period)
        self._prev: float | None = None
        self._flows: deque[tuple[float, float]] = deque(maxlen=period)

    def update(self, high: float, low: float, close: float, volume: float) -> float | None:
        tp = (high + low + close) / 3.0
        prev, self._prev = self._prev, tp
        if prev is None:
            return None
        raw = tp * volume
        self._flows.append((raw if tp > prev else 0.0, raw if tp < prev else 0.0))
        if len(self._flows) < self.period:
            return None
        pos = sum(f[0] for f in self._flows)
        neg = sum(f[1] for f in self._flows)
        if neg == 0:
            return 50.0 if pos == 0 else 100.0
        return 100.0 - 100.0 / (1.0 + pos / neg)
