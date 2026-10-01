"""Rolling covariance of returns with a benchmark (statistical family)."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingPairMoments


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
        self._update = self._rp.update_raw
        self._prev: tuple[float, float] | None = None

    def update(self, close: float, benchmark: float) -> float | None:
        prev, self._prev = self._prev, (close, benchmark)
        if prev is None:
            return None
        # simple_return inlined (hot path): cur / prev - 1, 0.0 when prev <= 0
        m = self._update(
            close / prev[0] - 1.0 if prev[0] > 0 else 0.0,
            benchmark / prev[1] - 1.0 if prev[1] > 0 else 0.0,
        )
        return None if m is None else m[4]
