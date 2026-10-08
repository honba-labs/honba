"""Opening-auction realism: the open is an auction print, not a continuous market.

Balch pitfall #8 ("Buying at the Open"): the opening price is a single
auction-clearing print formed by an overnight accumulation of imbalanced
orders, and the first minutes of trading are the most turbulent of the day.
Assuming effortless fills at ``Open[t]`` quietly flatters intraday entries.

Honba defends with two independent knobs on :class:`OpeningAuction` (pure
arithmetic — no I/O, no wall clock):

* **Spread buffer** (``spread_bps``). Every fill that happens at a printed open
  is degraded by an adverse buffer ``fraction = spread_bps / 10_000``: a buy pays
  ``open * (1 + fraction)``, a sell receives ``open * (1 - fraction)`` — the taker
  always crosses the auction's implied spread. Unlike
  :class:`honba.backtest.impact.MarketImpact` (pitfall #4) it is size-blind and
  needs no history: the auction is crossed whatever the order size. The two
  compose — their fractions stack on the same print.
* **Post-open delay** (``delay_bars``). An order waits this many *extra* driving
  bars after the session in which it was submitted before it becomes eligible to
  fill, so a sub-daily strategy can skip the first minutes of auction turbulence
  (5–15 minutes of one-minute bars is ``delay_bars=5..15``). The port counts one
  session per driving bar, so a delay is only meaningful for intraday timeframes
  and :func:`honba.backtest.simulated.make_simulator` refuses it for daily ones.

Both default to off: an :class:`OpeningAuction` with default fields is a no-op,
and passing ``auction=None`` (the default) leaves fills at the printed open.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["OpeningAuction"]


@dataclass(frozen=True, slots=True)
class OpeningAuction:
    """Adverse opening-spread buffer and post-open execution delay.

    Args:
        spread_bps: Buffer in basis points of the printed open, applied adversely
            to every open fill (>= 0 and < 10_000, so a sell price stays positive).
        delay_bars: Extra driving bars (sessions) an order waits after submission
            before it may fill (>= 0; intraday timeframes only).
    """

    spread_bps: float = 0.0
    delay_bars: int = 0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.spread_bps) and 0.0 <= self.spread_bps < 10_000.0):
            raise ValueError(
                f"spread_bps must be a finite number in [0, 10000), got {self.spread_bps}"
            )
        if (
            not isinstance(self.delay_bars, int)
            or isinstance(self.delay_bars, bool)
            or self.delay_bars < 0
        ):
            raise ValueError(f"delay_bars must be an int >= 0, got {self.delay_bars!r}")

    @property
    def fraction(self) -> float:
        """The adverse buffer as a fraction of the open (0 when disabled)."""
        return self.spread_bps / 10_000.0

    @property
    def enabled(self) -> bool:
        """True when either knob changes fill behaviour."""
        return self.spread_bps > 0.0 or self.delay_bars > 0
