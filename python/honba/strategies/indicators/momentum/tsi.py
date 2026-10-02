from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average import Ema


@dataclass(slots=True)
class TsiValue:
    tsi: float
    signal: float


@indicator(
    "tsi",
    "momentum",
    outputs=("tsi", "signal"),
    warmup=lambda s: s.long_length + s.short_length + s.signal_length - 1,
)
class Tsi(Indicator):
    """True Strength Index (TradingView): 100 * EMA_short(EMA_long(dx)) / EMA_short(EMA_long(|dx|)), dx = close change.

    EMAs are SMA-seeded (TradingView ta.ema); signal = EMA(tsi, signal_length). Zero denominator gives 0.
    """

    def __init__(
        self, long_length: int = 25, short_length: int = 13, signal_length: int = 13
    ) -> None:
        self.long_length = _check(long_length)
        self.short_length = _check(short_length)
        self.signal_length = _check(signal_length)
        self._prev: float | None = None
        self._n1, self._n2 = Ema(long_length), Ema(short_length)
        self._d1, self._d2 = Ema(long_length), Ema(short_length)
        self._sig = Ema(signal_length)

    def update(self, close: float) -> TsiValue | None:
        prev, self._prev = self._prev, close
        if prev is None:
            return None
        dx = close - prev
        n = self._n1.update(dx)
        d = self._d1.update(abs(dx))
        if n is None:
            return None
        n, d = self._n2.update(n), self._d2.update(d)
        if n is None:
            return None
        t = 100.0 * n / d if d != 0 else 0.0
        s = self._sig.update(t)
        return TsiValue(t, s) if s is not None else None
