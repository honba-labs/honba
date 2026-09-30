"""Arnaud Legoux moving average."""
from __future__ import annotations

import math
from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@indicator("alma", "moving_average", warmup=lambda s: s.period)
class Alma(Indicator):
    """ALMA: Gaussian-weighted MA, w_i = exp(-(i-m)^2 / (2 s^2)), m = floor(offset*(n-1)), s = n/sigma.

    i = 0 is the oldest bar in the window. Weights are normalised to sum to 1.
    """

    def __init__(self, period: int = 9, offset: float = 0.85, sigma: float = 6.0) -> None:
        self.period = _check(period)
        if not 0.0 <= offset <= 1.0:
            raise ValueError(f"offset must be in [0, 1], got {offset}")
        if sigma <= 0:
            raise ValueError(f"sigma must be > 0, got {sigma}")
        self.offset, self.sigma = float(offset), float(sigma)
        m, s = math.floor(offset * (period - 1)), period / sigma
        w = [math.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)]
        tot = sum(w)
        self._w = [v / tot for v in w]
        self._x: deque[float] = deque(maxlen=period)

    def update(self, x: float) -> float | None:
        self._x.append(x)
        if len(self._x) < self.period:
            return None
        return sum(w * v for w, v in zip(self._w, self._x))
