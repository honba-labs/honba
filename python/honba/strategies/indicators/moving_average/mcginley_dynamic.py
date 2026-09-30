"""McGinley Dynamic."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@indicator("mcginley_dynamic", "moving_average", warmup=lambda s: 1)
class McGinleyDynamic(Indicator):
    """McGinley Dynamic: md += (c - md) / (k * n * (c/md)^4), seeded with the first close.

    Convention: if md <= 0 the ratio is taken as 1; if the divisor is 0 (c == 0) md is unchanged.
    """

    def __init__(self, period: int = 14, k: float = 0.6) -> None:
        self.period = _check(period)
        if k <= 0:
            raise ValueError(f"k must be > 0, got {k}")
        self.k = float(k)
        self.value: float | None = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = float(x)
            return self.value
        md = self.value
        ratio = x / md if md > 0 else 1.0
        den = self.k * self.period * ratio ** 4
        if den > 0:
            self.value = md + (x - md) / den
        return self.value
