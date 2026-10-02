"""McClellan oscillator (breadth family)."""

from __future__ import annotations

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators.moving_average import Ema


@indicator("mcclellan_oscillator", "breadth", inputs=("advances", "declines"), warmup=lambda s: 1)
class McClellanOscillator(Indicator):
    """EMA(fast) - EMA(slow) of net advances (advances - declines), defaults 19/39.

    Both EMAs use alpha = 2/(n+1) seeded with the first net-advances value (seed="first"),
    so the oscillator is 0.0 on bar 1 and defined from then on.
    """

    def __init__(self, fast: int = 19, slow: int = 39) -> None:
        if fast < 1 or slow < 1:
            raise ValueError("fast and slow must be >= 1")
        if fast >= slow:
            raise ValueError(f"fast must be < slow, got {fast} >= {slow}")
        self.fast, self.slow = fast, slow
        self._f = Ema(fast, seed="first")
        self._s = Ema(slow, seed="first")

    def update(self, advances: float, declines: float) -> float:
        net = advances - declines
        return self._f.update(net) - self._s.update(net)
