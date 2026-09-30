"""Session-anchored VWAP with standard-deviation bands."""
from __future__ import annotations

import math
from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._india import ist_session_day


@dataclass(slots=True)
class VwapValue:
    vwap: float
    upper: float
    lower: float


@indicator("vwap", "volume", inputs=("high", "low", "close", "volume", "ts"),
           outputs=("vwap", "upper", "lower"), warmup=None)
class Vwap(Indicator):
    """Session VWAP on hlc3, reset each IST session day; bands = vwap +/- band_mult * volume-weighted stdev.
    Valid from the first bar of every session. Convention: while cumulative session volume is 0, vwap is
    the latest hlc3 and the stdev is 0."""

    def __init__(self, band_mult: float = 1.0) -> None:
        if band_mult < 0:
            raise ValueError(f"band_mult must be >= 0, got {band_mult}")
        self.band_mult = float(band_mult)
        self._day: int | None = None
        self._v = self._pv = self._pv2 = 0.0

    @property
    def warmup(self) -> None:
        return None  # session-anchored: a value exists from the first bar of each session

    def update(self, high: float, low: float, close: float, volume: float, ts: int) -> VwapValue:
        day = ist_session_day(int(ts))
        if day != self._day:
            self._day = day
            self._v = self._pv = self._pv2 = 0.0
        tp = (high + low + close) / 3.0
        self._v += volume
        self._pv += tp * volume
        self._pv2 += tp * tp * volume
        if self._v == 0:
            return VwapValue(tp, tp, tp)
        vw = self._pv / self._v
        sd = math.sqrt(max(0.0, self._pv2 / self._v - vw * vw))
        return VwapValue(vw, vw + self.band_mult * sd, vw - self.band_mult * sd)
