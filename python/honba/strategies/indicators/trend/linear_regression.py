"""Linear regression channel."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.moving_average.lsma import linreg_fit


@dataclass(frozen=True, slots=True)
class LinearRegressionValue:
    value: float
    slope: float
    upper: float
    lower: float


@indicator("linear_regression", "trend", outputs=("value", "slope", "upper", "lower"),
           warmup=lambda s: s.length)
class LinearRegression(Indicator):
    """Linear regression channel over ``length`` closes: value = end of the least-squares line,
    slope per bar, upper/lower = value +/- deviation * population std of the residuals.
    """

    def __init__(self, length: int = 100, upper_deviation: float = 2.0, lower_deviation: float = 2.0) -> None:
        if length < 2:
            raise ValueError(f"length must be >= 2, got {length}")
        if upper_deviation < 0 or lower_deviation < 0:
            raise ValueError("deviations must be >= 0")
        self.length = length
        self.upper_deviation, self.lower_deviation = float(upper_deviation), float(lower_deviation)
        self._w: deque[float] = deque(maxlen=length)

    def update(self, x: float) -> LinearRegressionValue | None:
        self._w.append(x)
        if len(self._w) < self.length:
            return None
        a, b = linreg_fit(self._w)
        n = self.length
        std = math.sqrt(sum((y - (a + b * i)) ** 2 for i, y in enumerate(self._w)) / n)
        end = a + b * (n - 1)
        return LinearRegressionValue(end, b, end + self.upper_deviation * std, end - self.lower_deviation * std)
