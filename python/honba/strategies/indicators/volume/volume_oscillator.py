"""Volume oscillator."""
from __future__ import annotations

from honba.strategies.indicators._util import check as _check
from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.moving_average import Ema


@indicator("volume_oscillator", "volume", inputs=("volume",), warmup=lambda s: max(s.short, s.long))
class VolumeOscillator(Indicator):
    """Volume oscillator: 100 * (EMA(volume, short) - EMA(volume, long)) / EMA(volume, long); 0 if the long EMA is 0."""

    def __init__(self, short: int = 5, long: int = 10) -> None:
        self.short = _check(short)
        self.long = _check(long)
        self._s = Ema(short)
        self._l = Ema(long)

    def update(self, volume: float) -> float | None:
        s, l = self._s.update(volume), self._l.update(volume)
        if s is None or l is None:
            return None
        return 100.0 * (s - l) / l if l else 0.0
