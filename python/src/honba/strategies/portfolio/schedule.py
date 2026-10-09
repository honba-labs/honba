"""Rebalance timing port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ScheduleState:
    """Facts available when deciding whether to rebalance on the current bar.

    ``bar_day``: UTC date of the bar. ``first_rebalance_done``: whether any rebalance has
    happened. ``trading_days_since_rebalance``: day rollovers seen since the last rebalance
    (or since the start). ``all_members_priced``: every current member has a price (only
    computed before the first rebalance, else ``False``). ``drift``: portfolio drift from
    target, or ``None`` when not measured (reserved for drift-triggered schedules).
    """

    bar_day: date
    first_rebalance_done: bool
    trading_days_since_rebalance: int
    all_members_priced: bool
    drift: float | None = None


@runtime_checkable
class RebalanceSchedule(Protocol):
    """Decide, per bar, whether to rebalance now. Must be pure."""

    def due(self, state: ScheduleState) -> bool: ...


class EveryNDays:
    """First rebalance once all members are priced (or on the next day rollover), then
    every ``n`` trading days. Matches ``TargetWeightStrategy``'s default cadence."""

    def __init__(self, n: int = 15) -> None:
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        self.n = n

    def due(self, state: ScheduleState) -> bool:
        if not state.first_rebalance_done:
            return state.all_members_priced or state.trading_days_since_rebalance >= 1
        return state.trading_days_since_rebalance >= self.n
