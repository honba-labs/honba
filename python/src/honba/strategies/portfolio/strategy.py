"""PortfolioStrategy: compose universe, selection, weighting and schedule."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any, ClassVar

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.declarative import TargetWeightStrategy
from honba.strategies.portfolio.schedule import EveryNDays, RebalanceSchedule, ScheduleState
from honba.strategies.portfolio.selection import SelectAll, Selector
from honba.strategies.portfolio.universe import Universe
from honba.strategies.portfolio.view import MarketView, PriceHistory
from honba.strategies.portfolio.weighting import EqualWeight, WeightingScheme

_EPOCH = date(1970, 1, 1)
_NS_PER_DAY = 86_400 * 10**9


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
        self._current_day: int | None = None
        self._previous_day: date | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, Any], name: str | None = None) -> PortfolioStrategy:
        """Build from plain TOML-friendly params; see ``portfolio.factory`` for the grammar."""
        from honba.strategies.portfolio.factory import build_portfolio_strategy

        return build_portfolio_strategy(params, name=name)

    @property
    def history_len(self) -> int:
        """Per-instrument close-history capacity (ring-buffer size) given at construction."""
        return self._history.maxlen

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
        if not done and self._warming_up():
            return False
        priced = False if done else self._all_priced()
        drift = self._drift() if done and getattr(self.schedule, "needs_drift", False) else None
        day = self._as_of()
        assert day is not None  # on_bar sets last_day before asking
        state = ScheduleState(day, done, st.days_since, priced, drift, self._previous_day)
        return self.schedule.due(state)

    def on_bar(self, bar: Bar) -> None:
        self._history.record(bar.instrument_id, bar.close)
        today = bar.ts // _NS_PER_DAY
        if today != self._current_day:
            self._previous_day = None if self._current_day is None else self._day(self._current_day)
            self._current_day = today
        super().on_bar(bar)

    # -- helpers ----------------------------------------------------------------
    def _view(self) -> MarketView:
        return self._history.view(self._tw.prices)

    def _as_of(self) -> date | None:
        last = self._tw.last_day
        return None if last is None else self._day(last)

    @staticmethod
    def _day(days: int) -> date:
        return _EPOCH + timedelta(days=days)

    def _all_priced(self) -> bool:
        """Every raw universe member (not just the selection) has a price."""
        members = self.universe_source.members(self._as_of())
        return set(members) <= self._tw.prices.keys()

    def _warming_up(self) -> bool:
        """Before the first rebalance: the universe has members but the selector keeps none.

        A selector that needs history (e.g. ``TopN``) would otherwise trigger an empty
        "initial" rebalance (then wait a full cadence); the schedule is not consulted until
        the selection is non-empty.
        """
        return bool(self.universe_source.members(self._as_of())) and not self.universe()

    def _drift(self) -> float | None:
        """Max |current weight - allocation * target weight| over members and held names,
        on cash-inclusive value at last prices; ``None`` if value <= 0."""
        value = self._portfolio_value()
        if value <= 0:
            return None
        targets = self.target_weights()
        prices = self._tw.prices
        held = {i: q for i, q in self.ctx.positions().items() if q != 0}
        worst = 0.0
        for iid in held.keys() | set(self.universe()):
            price = prices.get(iid)
            current = held.get(iid, 0.0) * price / value if price and price > 0 else 0.0
            worst = max(worst, abs(current - self.allocation * targets.get(iid, 0.0)))
        return worst
