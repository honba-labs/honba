"""Honba: Algorithmic trading and research system.

Public API surface: domain entities, strategy interfaces, indicators, screener, and wire models.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Public domain entities
from honba.domain import (
    AggressorSide,
    Bar,
    Instrument,
    InstrumentId,
    InstrumentKind,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    QuoteTick,
    TimeInForce,
    Trade,
    TradeTick,
)

# Screener
from honba.screener import (
    AmbiguousMetric,
    BarStore,
    CatalogError,
    CoverageRecord,
    CoverageStatus,
    DataEnsureResult,
    DataService,
    DateInterval,
    GapFetchPlan,
    InMemoryBarStore,
    InMemoryMarketDataProvider,
    MarketDataProvider,
    MetricCatalog,
    MetricResolutionError,
    MissingDataPolicy,
    OnMissingAction,
    ScreenerSource,
    UnknownMetric,
    load_catalog,
)

# Strategy & Indicators
from honba.strategies import (
    LedgerContext,
    Strategy,
    StrategyContext,
    indicators,
)

# Wire contracts (ADR 006)
from honba.wire import (
    ENUMS,
    MODELS,
    SCHEMA_VERSION,
    MetricDefinition,
    MetricKeySpec,
    MetricPeriod,
    MetricRef,
    Order,
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
    ScreenerRow,
    ScreenerScanRequest,
    ScreenerScanResponse,
    ScreenerSortSpec,
    Timeframe,
)

__all__ = [
    "AggressorSide",
    "AmbiguousMetric",
    "Bar",
    "BarStore",
    "CatalogError",
    "CoverageRecord",
    "CoverageStatus",
    "DataEnsureResult",
    "DataService",
    "DateInterval",
    "ENUMS",
    "GapFetchPlan",
    "InMemoryBarStore",
    "InMemoryMarketDataProvider",
    "Instrument",
    "InstrumentId",
    "InstrumentKind",
    "LedgerContext",
    "MODELS",
    "MarketDataProvider",
    "MetricCatalog",
    "MetricDefinition",
    "MetricKeySpec",
    "MetricPeriod",
    "MetricRef",
    "MetricResolutionError",
    "MissingDataPolicy",
    "OnMissingAction",
    "Order",
    "OrderIntent",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Portfolio",
    "Position",
    "QuoteTick",
    "SCHEMA_VERSION",
    "ScreenerFilterGroup",
    "ScreenerFilterPredicate",
    "ScreenerRow",
    "ScreenerScanRequest",
    "ScreenerScanResponse",
    "ScreenerSortSpec",
    "ScreenerSource",
    "Strategy",
    "StrategyContext",
    "TimeInForce",
    "Timeframe",
    "Trade",
    "TradeTick",
    "UnknownMetric",
    "__version__",
    "indicators",
    "load_catalog",
]
