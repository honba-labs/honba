"""RSI (momentum family)."""
from __future__ import annotations

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator


@indicator("rsi", "momentum", warmup=lambda s: s.period + 1)
class Rsi(Indicator):
    """Wilder RSI; first value after ``period + 1`` inputs. A window with no losses is 100."""

    def __init__(self, period: int = 14) -> None:
        self.period = _check(period)
        self._prev: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain = self._avg_loss = 0.0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self._prev is None:
            self._prev = x
            return None
        change, self._prev = x - self._prev, x
        gain, loss = max(change, 0.0), max(-change, 0.0)
        n = self.period
        if len(self._gains) < n:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < n:
                return None
            self._avg_gain, self._avg_loss = sum(self._gains) / n, sum(self._losses) / n
        else:
            self._avg_gain = (self._avg_gain * (n - 1) + gain) / n
            self._avg_loss = (self._avg_loss * (n - 1) + loss) / n
        if self._avg_loss == 0:
            self.value = 100.0
        else:
            self.value = 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)
        return self.value
