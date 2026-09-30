"""Rolling z-score (statistical family)."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._rolling import RollingMoments


@indicator("zscore", "statistical", warmup=lambda s: s.length)
class ZScore(Indicator):
    """(close - SMA) / population stdev over ``length`` bars (like ta.stdev, biased); 0.0 if stdev is 0."""

    def __init__(self, length: int = 20) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        self.length = length
        self._m = RollingMoments(length)

    def update(self, close: float) -> float | None:
        r = self._m.update(close)
        if r is None:
            return None
        return (close - r.mean) / r.std if r.std > 0 else 0.0
