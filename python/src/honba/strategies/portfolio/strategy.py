"""PortfolioStrategy: compose universe, selection, weighting and schedule."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import ClassVar

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.declarative import TargetWeightStrategy
from honba.strategies.portfolio.schedule import EveryNDays, RebalanceSchedule, ScheduleState
from honba.strategies.portfolio.selection import SelectAll, Selector
from honba.strategies.portfolio.universe import Universe
from honba.strategies.portfolio.view import MarketView, PriceHistory
from honba.strategies.portfolio.weighting import EqualWeight, WeightingScheme

_EPOCH = date(1970, 1, 1)


class PortfolioStrategy(TargetWeightStrategy):
    """Long-only portfolio built from swappable parts; the base class does the orders.

    Each rebalance: ``universe.members(as_of)`` -> ``selector.select`` -> ``weighting.weights``
    -> ``TargetWeightStrategy`` sizing in whole shares (exits, trims, then buys). ``schedule``
    decides when. ``allocation`` is the fraction of portfolio value to deploy. ``history_len``
    bounds the per-instrument closes kept for ``MarketView`` (default 64).

    ``name``: ``Strategy`` requires a class attribute ``name`` and the runner reads
    ``strategy.name`` from the instance, so this class defaults to ``"portfolio"`` and the
    constructor shadows it with an instance attribute (and re-points the logger) when
    ``name`` is given. No per-instance subclass is created.

    The universe and weights are re-evaluated at every call, so components must be
    deterministic and cheap.
    """

    name: ClassVar[str] = "portfolio"

    def __init__(
        self,
        universe: Universe,
        weighting: WeightingScheme | None = None,
        schedule: RebalanceSchedule | None = None,
        selector: Selector | None = None,
        *,
        allocation: float = 0.98,
        name: str | None = None,
        history_len: int = 64,
    ) -> None:
        if not (math.isfinite(allocation) and 0 < allocation <= 1):
            raise ValueError(f"allocation must be in (0, 1], got {allocation}")
        if history_len < 1:
            raise ValueError(f"history_len must be >= 1, got {history_len}")
        if name is not None:
            if not name.strip():
                raise ValueError("name must be a non-blank string")
            # Instance attribute shadows the ClassVar default (see class docstring).
            object.__setattr__(self, "name", name)
            self.logger = logging.getLogger(f"honba.strategy.{name}")
        self.universe_source = universe
        self.weighting = weighting if weighting is not None else EqualWeight()
        self.schedule = schedule if schedule is not None else EveryNDays(15)
        self.selector = selector if selector is not None else SelectAll()
        self.allocation = allocation  # type: ignore[misc]  # shadows the ClassVar default
        self._history = PriceHistory(history_len)

    # -- TargetWeightStrategy hooks ---------------------------------------------
    def universe(self) -> Iterable[InstrumentId]:
        """Selected members for the current bar date."""
        members = self.universe_source.members(self._as_of())
        return list(self.selector.select(members, self._view()))

    def target_weights(self) -> Mapping[InstrumentId, float]:
        """Weights for the selected names from the weighting scheme."""
        return self.weighting.weights(list(self.universe()), self._view())

    def should_rebalance(self, bar: Bar) -> bool:
        st = self._tw
        done = st.initial_done
        priced = False if done else set(self.universe()) <= st.prices.keys()
        day = self._as_of()
        assert day is not None  # on_bar sets last_day before asking
        return self.schedule.due(ScheduleState(day, done, st.days_since, priced, None))

    def on_bar(self, bar: Bar) -> None:
        self._history.record(bar.instrument_id, bar.close)
        super().on_bar(bar)

    # -- helpers ----------------------------------------------------------------
    def _view(self) -> MarketView:
        return self._history.view(self._tw.prices)

    def _as_of(self) -> date | None:
        last = self._tw.last_day
        return None if last is None else _EPOCH + timedelta(days=last)
