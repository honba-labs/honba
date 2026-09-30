"""Arms index / TRIN (breadth family)."""
from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


@indicator("trin", "breadth", inputs=("advances", "declines", "adv_volume", "dec_volume"), warmup=lambda s: 1)
class Trin(Indicator):
    """Arms index: (advances/declines) / (adv_volume/dec_volume).

    Convention: if declines, adv_volume or dec_volume is <= 0 the ratio is undefined and
    the neutral value 1.0 is returned. Values > 1 are bearish, < 1 bullish.
    """

    def __init__(self) -> None:
        pass

    def update(self, advances: float, declines: float, adv_volume: float, dec_volume: float) -> float:
        if declines <= 0 or adv_volume <= 0 or dec_volume <= 0:
            return 1.0
        return (advances / declines) / (adv_volume / dec_volume)
