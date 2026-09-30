"""Shared rolling helpers for the volatility family (O(1) per update, numerically stable)."""
from __future__ import annotations

from honba.strategies.indicators._rolling import RollingMoments


class RollingStd:
    """Population (biased, /n) standard deviation and mean over the last ``period`` values."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._m = RollingMoments(period)

    def update(self, x: float) -> tuple[float, float] | None:
        """Returns ``(mean, stdev)`` once ``period`` values are seen, else None."""
        r = self._m.update(x)
        return None if r is None else (r.mean, r.std)
