"""Composable portfolio construction: universe, selection, weighting, schedule.

``PortfolioStrategy(universe, weighting, schedule, selector)`` wires the parts into a
``TargetWeightStrategy``. Each part is a small ``Protocol`` with simple implementations.
"""

from honba.strategies.portfolio.schedule import EveryNDays, RebalanceSchedule, ScheduleState
from honba.strategies.portfolio.selection import SelectAll, Selector
from honba.strategies.portfolio.strategy import PortfolioStrategy
from honba.strategies.portfolio.universe import NamedUniverse, StaticUniverse, Universe
from honba.strategies.portfolio.view import MarketView, PriceHistory
from honba.strategies.portfolio.weighting import EqualWeight, WeightingScheme

__all__ = [
    "EqualWeight",
    "EveryNDays",
    "MarketView",
    "NamedUniverse",
    "PortfolioStrategy",
    "PriceHistory",
    "RebalanceSchedule",
    "ScheduleState",
    "SelectAll",
    "Selector",
    "StaticUniverse",
    "Universe",
    "WeightingScheme",
]
