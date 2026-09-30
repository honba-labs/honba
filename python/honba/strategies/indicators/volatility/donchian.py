"""Donchian channel (volatility family)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from honba.strategies.indicators._util import check as _check


@dataclass(frozen=True, slots=True)
class DonchianValue:
    upper: float
    lower: float


class Donchian:
    """Highest high / lowest low over the last ``period`` bars, including the current one."""

    def __init__(self, period: int = 20) -> None:
        self.period = _check(period)
        self._h: deque[float] = deque(maxlen=period)
        self._l: deque[float] = deque(maxlen=period)

    def update(self, high: float, low: float) -> DonchianValue | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self.period:
            return None
        return DonchianValue(max(self._h), min(self._l))
