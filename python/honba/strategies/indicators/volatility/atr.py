"""Average true range (volatility family)."""
from __future__ import annotations

from honba.strategies.indicators._util import check as _check


class Atr:
    """Wilder ATR. By default the first true range needs a previous close, so the first
    value appears after ``period + 1`` bars; ``include_first_bar=True`` (Jesse) uses
    high - low for the first bar, giving the first value after ``period`` bars."""

    def __init__(self, period: int = 14, include_first_bar: bool = False) -> None:
        self.period = _check(period)
        self.include_first_bar = include_first_bar
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        prev, self._prev_close = self._prev_close, close
        if prev is None:
            if not self.include_first_bar:
                return None
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev), abs(low - prev))
        if self.value is not None:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        else:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self.value = sum(self._trs) / self.period
        return self.value
