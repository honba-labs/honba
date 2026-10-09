"""Composable portfolio construction: universe, selection, weighting, schedule.

``PortfolioStrategy(universe, weighting, schedule, selector)`` wires the parts into a
``TargetWeightStrategy``. Each part is a small ``Protocol`` with simple implementations.
"""

from honba.strategies.portfolio.factory import build_portfolio_strategy
from honba.strategies.portfolio.schedule import (
    AnyOf,
    DriftBand,
    EveryNDays,
    MonthlyFirstSession,
    RebalanceSchedule,
    ScheduleState,
)
from honba.strategies.portfolio.scoring import low_volatility, mean_reversion, momentum
from honba.strategies.portfolio.selection import SelectAll, Selector, TopN
from honba.strategies.portfolio.strategy import PortfolioStrategy
from honba.strategies.portfolio.universe import NamedUniverse, StaticUniverse, Universe
from honba.strategies.portfolio.view import MarketView, PriceHistory
from honba.strategies.portfolio.weighting import EqualWeight, InverseVolatility, WeightingScheme

__all__ = [
    "AnyOf",
    "DriftBand",
    "EqualWeight",
    "EveryNDays",
    "InverseVolatility",
    "MarketView",
    "MonthlyFirstSession",
    "NamedUniverse",
    "PortfolioStrategy",
    "PriceHistory",
    "RebalanceSchedule",
    "ScheduleState",
    "SelectAll",
    "Selector",
    "StaticUniverse",
    "TopN",
    "Universe",
    "WeightingScheme",
    "build_portfolio_strategy",
    "low_volatility",
    "mean_reversion",
    "momentum",
]
