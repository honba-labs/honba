"""MACD (trend family)."""
from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.trend.moving_average import Ema


@dataclass(frozen=True, slots=True)
class MacdValue:
    macd: float
    signal: float
    histogram: float


class Macd:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9, seed: str = "sma") -> None:
        if not 0 < _check(fast) < _check(slow):
            raise ValueError(f"fast must be less than slow, got {fast} and {slow}")
        self._fast, self._slow, self._signal = Ema(fast, seed), Ema(slow, seed), Ema(_check(signal), seed)

    def update(self, x: float) -> MacdValue | None:
        f, s = self._fast.update(x), self._slow.update(x)
        if f is None or s is None:
            return None
        line = f - s
        sig = self._signal.update(line)
        if sig is None:
            return None
        return MacdValue(line, sig, line - sig)
