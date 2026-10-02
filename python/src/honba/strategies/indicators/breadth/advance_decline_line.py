"""Advance/Decline line (breadth family)."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


@indicator("advance_decline_line", "breadth", inputs=("advances", "declines"), warmup=lambda s: 1)
class AdvanceDeclineLine(Indicator):
    """Cumulative sum of (advances - declines); starts at the first bar's net advances."""

    def __init__(self) -> None:
        self._total = 0.0

    def update(self, advances: float, declines: float) -> float:
        self._total += advances - declines
        return self._total
