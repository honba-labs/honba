"""Moving-average envelope (volatility family)."""

from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Ema, Sma


@dataclass(frozen=True, slots=True)
class EnvelopeValue:
    upper: float
    middle: float
    lower: float


@indicator(
    "envelope", "volatility", outputs=("upper", "middle", "lower"), warmup=lambda s: s.length
)
class Envelope(Indicator):
    """Envelope: MA(close, length) * (1 +/- percent/100); ``ma_type`` is 'sma' (default) or 'ema' (SMA-seeded)."""

    def __init__(self, length: int = 20, percent: float = 10.0, ma_type: str = "sma") -> None:
        self.length = _check(length)
        if percent < 0:
            raise ValueError("percent must be >= 0")
        if ma_type not in ("sma", "ema"):
            raise ValueError(f"ma_type must be 'sma' or 'ema', got {ma_type!r}")
        self.percent, self.ma_type = percent, ma_type
        self._ma = Sma(length) if ma_type == "sma" else Ema(length)

    def update(self, close: float) -> EnvelopeValue | None:
        m = self._ma.update(close)
        if m is None:
            return None
        k = self.percent / 100.0
        return EnvelopeValue(m * (1 + k), m, m * (1 - k))
