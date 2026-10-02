from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average import Sma


@indicator(
    "awesome_oscillator", "momentum", inputs=("high", "low"), warmup=lambda s: max(s.fast, s.slow)
)
class AwesomeOscillator(Indicator):
    """Awesome Oscillator: SMA(fast) - SMA(slow) of the median price (high + low) / 2."""

    def __init__(self, fast: int = 5, slow: int = 34) -> None:
        self.fast = _check(fast)
        self.slow = _check(slow)
        self._f, self._s = Sma(fast), Sma(slow)

    def update(self, high: float, low: float) -> float | None:
        m = (high + low) / 2.0
        f, s = self._f.update(m), self._s.update(m)
        return f - s if f is not None and s is not None else None
