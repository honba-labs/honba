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

# Data Loaders
from honba.data.loaders import (
    NseBhavcopyProvider,
    YFinanceProvider,
)

# Strategy & Indicators
from honba.strategies import (
    LedgerContext,
    Strategy,
    StrategyContext,
    indicators,
)

# Logging & Events
from honba.log import EventFilter, KNOWN_EVENTS, register_event, setup_event_logging

# Formatting utilities
from honba.utils.format import (
    format_currency,
    format_inr,
    format_usd,
    format_eur,
    format_date_indian,
    format_date_us,
    format_date_iso,
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
    "EventFilter",
    "GapFetchPlan",
    "KNOWN_EVENTS",
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
    "NseBhavcopyProvider",
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
    "YFinanceProvider",
    "__version__",
    "indicators",
    "load_catalog",
    "register_event",
    "setup_event_logging",
    "format_currency",
    "format_inr",
    "format_usd",
    "format_eur",
    "format_date_indian",
    "format_date_us",
    "format_date_iso",
]
