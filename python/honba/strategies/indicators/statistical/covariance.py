"""Rolling covariance of returns with a benchmark (statistical family)."""
from __future__ import annotations

from collections import deque

from honba.strategies.indicators.statistical._pair import cov_var, simple_return
from honba.strategies.indicators._base import Indicator, indicator


@indicator("covariance", "statistical", inputs=("close", "benchmark"), warmup=lambda s: s.length + 1)
class Covariance(Indicator):
    """Sample covariance (divisor n-1) of asset and benchmark simple returns over ``length`` returns."""

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._x: deque[float] = deque(maxlen=length)
        self._y: deque[float] = deque(maxlen=length)
        self._prev: tuple[float, float] | None = None

    def update(self, close: float, benchmark: float) -> float | None:
        prev, self._prev = self._prev, (close, benchmark)
        if prev is None:
            return None
        self._x.append(simple_return(prev[0], close))
        self._y.append(simple_return(prev[1], benchmark))
        if len(self._x) < self.length:
            return None
        return cov_var(self._x, self._y, 1)[0]
