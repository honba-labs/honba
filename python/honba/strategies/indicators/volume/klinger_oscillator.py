"""Klinger volume oscillator."""
from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.moving_average import Ema


@dataclass(slots=True)
class KlingerValue:
    klinger: float
    signal: float


@indicator("klinger_oscillator", "volume", inputs=("high", "low", "close", "volume"),
           outputs=("klinger", "signal"), warmup=lambda s: max(s.fast, s.slow) + s.signal)
class KlingerOscillator(Indicator):
    """Klinger oscillator (TradingView form): signed volume = +V if hlc3 >= previous hlc3 else -V;
    klinger = EMA(sv, fast) - EMA(sv, slow); signal = EMA(klinger, signal). EMAs are SMA-seeded;
    the first bar has no previous hlc3 so signed volume starts on bar 2."""

    def __init__(self, fast: int = 34, slow: int = 55, signal: int = 13) -> None:
        self.fast, self.slow, self.signal = _check(fast), _check(slow), _check(signal)
        self._f, self._s, self._sig = Ema(fast), Ema(slow), Ema(signal)
        self._prev: float | None = None

    def update(self, high: float, low: float, close: float, volume: float) -> KlingerValue | None:
        tp = (high + low + close) / 3.0
        prev, self._prev = self._prev, tp
        if prev is None:
            return None
        sv = volume if tp >= prev else -volume
        f, s = self._f.update(sv), self._s.update(sv)
        if f is None or s is None:
            return None
        k = f - s
        sig = self._sig.update(k)
        return None if sig is None else KlingerValue(k, sig)
