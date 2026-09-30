"""Rolling covariance of returns with a benchmark (statistical family)."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingPairMoments
from honba.strategies.indicators.statistical._pair import simple_return


@indicator("covariance", "statistical", inputs=("close", "benchmark"), warmup=lambda s: s.length + 1)
class Covariance(Indicator):
    """Sample covariance (divisor n-1) of asset and benchmark simple returns over ``length`` returns.

    O(1) per update (``RollingPairMoments``, ddof=1); NaN while a non-finite or ``|v| > 1e150``
    return is in either window, exact again once it has left.
    """

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._rp = RollingPairMoments(length, ddof=1)
        self._prev: tuple[float, float] | None = None

    def update(self, close: float, benchmark: float) -> float | None:
        prev, self._prev = self._prev, (close, benchmark)
        if prev is None:
            return None
        m = self._rp.update(simple_return(prev[0], close), simple_return(prev[1], benchmark))
        return None if m is None else m.cov
