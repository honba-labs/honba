"""Rolling beta against a benchmark (statistical family)."""

from __future__ import annotations

import math

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingPairMoments


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
        if m is None:
            return None
        cov, vy = m[4], m[3]
        if cov != cov:  # noqa: PLR0124  (NaN check)
            return math.nan
        return cov / vy if vy > 0 else 0.0
