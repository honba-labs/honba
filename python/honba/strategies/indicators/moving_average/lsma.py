"""Least-squares (linear regression) moving average."""
from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from honba.strategies.indicators._base import Indicator, indicator


def linreg_fit(ys: Sequence[float]) -> tuple[float, float]:
    """Least-squares line through (0, ys[0]) .. (n-1, ys[-1]) as (intercept, slope); n >= 2."""
    n = len(ys)
    sx = n * (n - 1) / 2.0
    sxx = (n - 1) * n * (2 * n - 1) / 6.0
    sy = sxy = 0.0
    for i, y in enumerate(ys):
        sy += y
        sxy += i * y
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return (sy - slope * sx) / n, slope


@indicator("lsma", "moving_average", warmup=lambda s: s.period)
class Lsma(Indicator):
    """LSMA: endpoint of the least-squares line over ``period`` bars, shifted by ``offset`` bars.

    value = intercept + slope * (period - 1 - offset) (TradingView ta.linreg).
    """

    def __init__(self, period: int = 25, offset: int = 0) -> None:
        if period < 2:
            raise ValueError(f"period must be >= 2, got {period}")
        self.period, self.offset = period, offset
        self._w: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> float | None:
        self._w.append(x)
        if len(self._w) < self.period:
            return None
        a, b = linreg_fit(self._w)
        return a + b * (self.period - 1 - self.offset)
