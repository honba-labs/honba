"""Accumulation/distribution line."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


def clv_volume(high: float, low: float, close: float, volume: float) -> float:
    """Money-flow volume ``((C-L)-(H-C))/(H-L) * V``; 0 when ``high == low``."""
    rng = high - low
    if rng == 0:
        return 0.0
    return ((close - low) - (high - close)) / rng * volume


@indicator(
    "accumulation_distribution",
    "volume",
    inputs=("high", "low", "close", "volume"),
    warmup=lambda s: 1,
)
class AccumulationDistribution(Indicator):
    """Accumulation/distribution: cumulative close-location-value times volume (CLV = 0 when high == low)."""

    def __init__(self) -> None:
        self.value = 0.0

    def update(self, high: float, low: float, close: float, volume: float) -> float:
        self.value += clv_volume(high, low, close, volume)
        return self.value
