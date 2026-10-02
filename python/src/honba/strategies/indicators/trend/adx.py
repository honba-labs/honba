"""Average directional index (Wilder)."""

from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.moving_average.averages import Rma


@dataclass(frozen=True, slots=True)
class AdxValue:
    adx: float
    plus_di: float
    minus_di: float


@indicator(
    "adx",
    "trend",
    inputs=("high", "low", "close"),
    outputs=("adx", "plus_di", "minus_di"),
    warmup=lambda s: s.di_length + s.adx_smoothing,
)
class Adx(Indicator):
    """ADX/DMI (TradingView ta.dmi): Wilder RMA of TR, +DM, -DM; DI = 100*RMA(DM)/RMA(TR);
    ADX = RMA(100*|+DI - -DI| / (+DI + -DI), adx_smoothing).

    RMAs are SMA-seeded. The first bar only supplies the previous high/low/close, so the DIs
    appear after ``di_length + 1`` bars and ADX after ``di_length + adx_smoothing`` bars.
    A zero DI sum counts as 1 in the denominator (DX = 0).
    """

    def __init__(self, di_length: int = 14, adx_smoothing: int = 14) -> None:
        self.di_length, self.adx_smoothing = _check(di_length), _check(adx_smoothing)
        self._tr, self._pdm, self._mdm = Rma(di_length), Rma(di_length), Rma(di_length)
        self._adx = Rma(adx_smoothing)
        self._prev: tuple[float, float, float] | None = None

    def update(self, high: float, low: float, close: float) -> AdxValue | None:
        prev, self._prev = self._prev, (high, low, close)
        if prev is None:
            return None
        ph, pl, pc = prev
        up, down = high - ph, pl - low
        pdm = up if up > down and up > 0 else 0.0
        mdm = down if down > up and down > 0 else 0.0
        tr = max(high - low, abs(high - pc), abs(low - pc))
        t, p, m = self._tr.update(tr), self._pdm.update(pdm), self._mdm.update(mdm)
        if t is None:
            return None
        pdi = 100.0 * p / t if t > 0 else 0.0
        mdi = 100.0 * m / t if t > 0 else 0.0
        s = pdi + mdi
        a = self._adx.update(100.0 * abs(pdi - mdi) / (s if s != 0 else 1.0))
        return None if a is None else AdxValue(a, pdi, mdi)
