"""Up/Down volume ratio (breadth family)."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator


@indicator(
    "updown_volume_ratio", "breadth", inputs=("adv_volume", "dec_volume"), warmup=lambda s: 1
)
class UpDownVolumeRatio(Indicator):
    """Advancing volume / declining volume; neutral 1.0 when declining volume is <= 0."""

    def __init__(self) -> None:
        pass

    def update(self, adv_volume: float, dec_volume: float) -> float:
        return adv_volume / dec_volume if dec_volume > 0 else 1.0
