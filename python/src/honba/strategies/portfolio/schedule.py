"""Rebalance timing port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ScheduleState:
    """Facts available when deciding whether to rebalance on the current bar.

    ``bar_day``: UTC date of the bar. ``first_rebalance_done``: whether any rebalance has
    happened. ``trading_days_since_rebalance``: day rollovers seen since the last rebalance
    (or since the start). ``all_members_priced``: every current member has a price (only
    computed before the first rebalance, else ``False``). ``drift``: portfolio drift from
    target (max absolute gap between current and target weight), or ``None`` when not
    measured: it is only computed after the first rebalance and only when the schedule has
    ``needs_drift = True``. ``previous_bar_day``: the UTC date of the last distinct day seen
    before ``bar_day`` (``None`` on the first day).
    """

    bar_day: date
    first_rebalance_done: bool
    trading_days_since_rebalance: int
    all_members_priced: bool
    drift: float | None = None
    previous_bar_day: date | None = None


@runtime_checkable
class RebalanceSchedule(Protocol):
    """Decide, per bar, whether to rebalance now. Must be pure.

    Optional class attribute ``needs_drift: bool`` (default ``False`` when absent): set it
    ``True`` to have the strategy compute ``ScheduleState.drift`` each bar (costly, so opt-in).
    """

    def due(self, state: ScheduleState) -> bool: ...


def _initial_due(state: ScheduleState) -> bool:
    """First-rebalance rule shared by all schedules (same as ``EveryNDays``)."""
    return state.all_members_priced or state.trading_days_since_rebalance >= 1


class EveryNDays:
    """First rebalance once all members are priced (or on the next day rollover), then
    every ``n`` trading days. Matches ``TargetWeightStrategy``'s default cadence."""

    needs_drift = False

    def __init__(self, n: int = 15) -> None:
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        self.n = n

    def due(self, state: ScheduleState) -> bool:
        if not state.first_rebalance_done:
            return _initial_due(state)
        return state.trading_days_since_rebalance >= self.n


class MonthlyFirstSession:
    """Initial rebalance as in ``EveryNDays``, then on the first bar of each new UTC calendar
    month (the bar whose day is in a different month than the previous distinct day).
    A rebalance already done that day (counter reset) is not repeated by later bars."""

    needs_drift = False

    def due(self, state: ScheduleState) -> bool:
        if not state.first_rebalance_done:
            return _initial_due(state)
        prev = state.previous_bar_day
        if prev is None or state.trading_days_since_rebalance < 1:
            return False
        return (prev.year, prev.month) != (state.bar_day.year, state.bar_day.month)


class DriftBand:
    """Initial rebalance as in ``EveryNDays``, then whenever ``state.drift >= tolerance``
    (fraction of portfolio value, e.g. ``0.05``), but never on the day of the previous
    rebalance (its orders have not filled yet, so drift would still look large).
    Sets ``needs_drift = True``."""

    needs_drift = True

    def __init__(self, tolerance: float) -> None:
        if not (isfinite(tolerance) and tolerance > 0):
            raise ValueError(f"tolerance must be finite and > 0, got {tolerance}")
        self.tolerance = tolerance

    def due(self, state: ScheduleState) -> bool:
        if not state.first_rebalance_done:
            return _initial_due(state)
        if state.drift is None or state.trading_days_since_rebalance < 1:
            return False
        return state.drift >= self.tolerance


class AnyOf:
    """Due when any child schedule is due (all children are evaluated each bar).
    ``needs_drift`` is true if any child needs it, e.g. ``AnyOf(MonthlyFirstSession(),
    DriftBand(0.05))``."""

    def __init__(self, *schedules: RebalanceSchedule) -> None:
        if not schedules:
            raise ValueError("AnyOf needs at least one schedule")
        self.schedules = tuple(schedules)
        self.needs_drift = any(getattr(s, "needs_drift", False) for s in schedules)

    def due(self, state: ScheduleState) -> bool:
        results = [s.due(state) for s in self.schedules]  # no short-circuit
        return any(results)
