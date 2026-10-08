"""Square-root market impact: the printed open is not the price you get.

Balch pitfall #4 ("Ignoring Market Impact"): assuming frictionless, size-blind
fills at the last print turns strategies that only work with infinite liquidity
into apparent winners. The model here is the standard square-root law from
impact #4's documentation:

    impact_fraction = kappa * sigma_daily * sqrt(quantity / ADV)

``sigma_daily`` is the sample standard deviation of close-to-close returns over
the rolling ``window``, ``ADV`` the mean volume over the same window, and the
result is a *fraction* of the fill price — a buy pays it, a sell receives less
of it. ``kappa`` is the security's impact coefficient (higher for anything
thin or jumpy); 0 disables impact without changing the simulator.

History only ever comes from sessions the simulator has already seen
(:meth:`MarketImpact.observe` is called after a session's fills), so a fill
never prices itself off its own bar. Pure: no I/O, no wall clock.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise

from honba.entities.instrument import InstrumentId

__all__ = ["MarketImpact"]


@dataclass
class MarketImpact:
    """Rolling square-root impact model, tracked per instrument.

    Args:
        kappa: Impact coefficient (>= 0). ``0`` means no impact anywhere.
        window: Sessions of volume/close history to keep (>= 2, so a daily
            volatility exists).
    """

    kappa: float = 1.0
    window: int = 20
    _volumes: dict[InstrumentId, deque[float]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _closes: dict[InstrumentId, deque[float]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not (math.isfinite(self.kappa) and self.kappa >= 0.0):
            raise ValueError(f"kappa must be a finite number >= 0, got {self.kappa}")
        if self.window < 2:
            raise ValueError(f"window must be >= 2 so a daily sigma exists, got {self.window}")

    def observe(self, instrument_id: InstrumentId, *, volume: float, close: float) -> None:
        """Record one finished session's volume and close for ``instrument_id``.

        Called after the session's fills, so the oldest usable history for a
        fill at session *k+1* ends at session *k*.
        """
        volumes = self._volumes.get(instrument_id)
        closes = self._closes.get(instrument_id)
        if volumes is None:
            volumes = deque(maxlen=self.window)
            self._volumes[instrument_id] = volumes
        if closes is None:
            closes = deque(maxlen=self.window)
            self._closes[instrument_id] = closes
        volumes.append(volume)
        closes.append(close)

    def adv(self, instrument_id: InstrumentId) -> float:
        """Average daily volume over the window (0 when nothing is observed)."""
        volumes = self._volumes.get(instrument_id)
        if not volumes:
            return 0.0
        return statistics.fmean(volumes)

    def sigma(self, instrument_id: InstrumentId) -> float:
        """Sample standard deviation of close-to-close returns (0 when undefined)."""
        closes = self._closes.get(instrument_id)
        if closes is None or len(closes) < 2:
            return 0.0
        returns = [after / before - 1.0 for before, after in pairwise(closes) if before > 0.0]
        if len(returns) < 2:
            return 0.0
        return statistics.stdev(returns)

    def fraction(self, instrument_id: InstrumentId, quantity: float) -> float:
        """``kappa * sigma_daily * sqrt(quantity / ADV)`` — 0 without usable history."""
        if quantity <= 0.0:
            return 0.0
        adv = self.adv(instrument_id)
        if adv <= 0.0:
            return 0.0
        sigma = self.sigma(instrument_id)
        if sigma <= 0.0:
            return 0.0
        return self.kappa * sigma * math.sqrt(quantity / adv)
