"""Keltner channels (volatility family)."""

from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Ema
from honba.strategies.indicators.volatility.atr import Atr


@dataclass(frozen=True, slots=True)
class KeltnerValue:
    upper: float
    middle: float
    lower: float


@indicator(
    "keltner",
    "volatility",
    inputs=("high", "low", "close"),
    outputs=("upper", "middle", "lower"),
    warmup=lambda s: max(s.length, s.atr_length + 1),
)
class Keltner(Indicator):
    """Keltner channels: EMA(close, length) +/- mult * Wilder ATR(atr_length) (TradingView defaults).

    The EMA is SMA-seeded; ATR needs a previous close so it appears after ``atr_length + 1`` bars."""

    def __init__(self, length: int = 20, atr_length: int = 10, mult: float = 2.0) -> None:
        self.length, self.atr_length = _check(length), _check(atr_length)
        if mult < 0:
            raise ValueError("mult must be >= 0")
        self.mult = mult
        self._ema, self._atr = Ema(length), Atr(atr_length)

    def update(self, high: float, low: float, close: float) -> KeltnerValue | None:
        mid, atr = self._ema.update(close), self._atr.update(high, low, close)
        if mid is None or atr is None:
            return None
        return KeltnerValue(mid + self.mult * atr, mid, mid - self.mult * atr)
