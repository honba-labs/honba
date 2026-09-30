"""Parabolic SAR."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


@indicator("parabolic_sar", "trend", inputs=("high", "low"), warmup=lambda s: 2)
class ParabolicSar(Indicator):
    """Wilder's Parabolic SAR: sar += af*(ep - sar); af starts at ``start``, grows by
    ``increment`` on each new extreme point, capped at ``maximum``.

    The direction of the second bar is long unless the downward move exceeds the upward move
    (TA-Lib's -DM rule); the initial SAR is the first bar's low (long) or high (short). SAR never
    enters the previous two bars' range; on reversal it jumps to the prior extreme point.
    """

    def __init__(self, start: float = 0.02, increment: float = 0.02, maximum: float = 0.2) -> None:
        if start <= 0 or increment <= 0:
            raise ValueError("start and increment must be > 0")
        if maximum < start:
            raise ValueError(f"maximum must be >= start, got {maximum} < {start}")
        self.start, self.increment, self.maximum = float(start), float(increment), float(maximum)
        self._p1: tuple[float, float] | None = None  # previous bar (high, low)
        self._p2: tuple[float, float] | None = None  # bar before that
        self._long = True
        self._sar = self._ep = 0.0
        self._af = self.start

    def update(self, high: float, low: float) -> float | None:
        p1, p2 = self._p1, self._p2
        self._p2, self._p1 = p1, (high, low)
        if p1 is None:
            return None
        if p2 is None:  # second bar: initialise
            up, down = high - p1[0], p1[1] - low
            self._long = not (down > 0 and up < down)
            self._sar = p1[1] if self._long else p1[0]
            self._ep = high if self._long else low
            self._af = self.start
            return self._sar
        sar = self._sar + self._af * (self._ep - self._sar)
        if self._long:
            sar = min(sar, p1[1], p2[1])
            if low < sar:  # reverse to short
                self._long, sar, self._ep, self._af = False, max(self._ep, high), low, self.start
            elif high > self._ep:
                self._ep, self._af = high, min(self._af + self.increment, self.maximum)
        else:
            sar = max(sar, p1[0], p2[0])
            if high > sar:  # reverse to long
                self._long, sar, self._ep, self._af = True, min(self._ep, low), high, self.start
            elif low < self._ep:
                self._ep, self._af = low, min(self._af + self.increment, self.maximum)
        self._sar = sar
        return sar
