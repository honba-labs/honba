"""Rolling beta against a benchmark (statistical family)."""
from __future__ import annotations

import math

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingPairMoments
from honba.strategies.indicators.statistical._pair import simple_return


@indicator("beta", "statistical", inputs=("close", "benchmark"), warmup=lambda s: s.length + 1)
class Beta(Indicator):
    """Slope of asset simple returns on benchmark simple returns over ``length`` returns.

    beta = cov(asset, bench) / var(bench); needs length+1 prices. Returns 0.0 if the
    benchmark return variance is zero. Benchmark is e.g. NIFTY 50.

    O(1) per update (``RollingPairMoments`` over the returns); NaN while a non-finite or
    ``|v| > 1e150`` return is in either window (the old code returned 0.0 when only the benchmark
    window was non-finite), exact again once it has left.
    """

    def __init__(self, length: int = 60) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._rp = RollingPairMoments(length)
        self._prev: tuple[float, float] | None = None

    def update(self, close: float, benchmark: float) -> float | None:
        prev, self._prev = self._prev, (close, benchmark)
        if prev is None:
            return None
        m = self._rp.update(simple_return(prev[0], close), simple_return(prev[1], benchmark))
        if m is None:
            return None
        if math.isnan(m.cov):
            return math.nan
        return m.cov / m.var_y if m.var_y > 0 else 0.0
