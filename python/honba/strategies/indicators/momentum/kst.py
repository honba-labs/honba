from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators.momentum.roc import Roc
from honba.strategies.indicators.moving_average import Sma


@dataclass(slots=True)
class KstValue:
    kst: float
    signal: float


@indicator(
    "kst",
    "momentum",
    outputs=("kst", "signal"),
    warmup=lambda s: (
        max(
            a + b
            for a, b in zip((s.roc1, s.roc2, s.roc3, s.roc4), (s.sma1, s.sma2, s.sma3, s.sma4))
        )
        + s.signal
        - 1
    ),
)
class Kst(Indicator):
    """Know Sure Thing (TradingView): 1*SMA(ROC1,S1) + 2*SMA(ROC2,S2) + 3*SMA(ROC3,S3) + 4*SMA(ROC4,S4); signal = SMA(kst, signal).

    Defaults ROC 10/15/20/30, SMA 10/10/10/15, signal 9. ROC is percent (see ``roc``).
    """

    def __init__(
        self,
        roc1: int = 10,
        roc2: int = 15,
        roc3: int = 20,
        roc4: int = 30,
        sma1: int = 10,
        sma2: int = 10,
        sma3: int = 10,
        sma4: int = 15,
        signal: int = 9,
    ) -> None:
        self.roc1, self.roc2, self.roc3, self.roc4 = (
            _check(roc1),
            _check(roc2),
            _check(roc3),
            _check(roc4),
        )
        self.sma1, self.sma2, self.sma3, self.sma4 = (
            _check(sma1),
            _check(sma2),
            _check(sma3),
            _check(sma4),
        )
        self.signal = _check(signal)
        self._rocs = [Roc(r) for r in (roc1, roc2, roc3, roc4)]
        self._smas = [Sma(s) for s in (sma1, sma2, sma3, sma4)]
        self._sig = Sma(signal)

    def update(self, close: float) -> KstValue | None:
        parts = []
        for roc, sma in zip(self._rocs, self._smas):
            r = roc.update(close)
            parts.append(sma.update(r) if r is not None else None)
        if any(p is None for p in parts):
            return None
        kst = sum(w * p for w, p in enumerate(parts, 1))
        s = self._sig.update(kst)
        return KstValue(kst, s) if s is not None else None
