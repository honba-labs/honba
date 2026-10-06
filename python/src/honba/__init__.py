"""Honba: Algorithmic trading and research system.

Public API surface: domain entities, strategy interfaces, indicators, screener, and wire models.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

# Public domain entities
# Data Loaders
from honba.data.loaders import (
    NseBhavcopyProvider,
    YFinanceProvider,
)
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

# Logging & Events
from honba.log import KNOWN_EVENTS, EventFilter, register_event, setup_event_logging

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

# Formatting utilities
from honba.utils.format import (
    format_currency,
    format_date_indian,
    format_date_iso,
    format_date_us,
    format_eur,
    format_inr,
    format_usd,
)

# Wire contracts (ADR 006)
from honba.wire import (
    ENUMS,
    MODELS,
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
    "ENUMS",
    "KNOWN_EVENTS",
    "MODELS",
    "SCHEMA_VERSION",
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
    "EventFilter",
    "GapFetchPlan",
    "InMemoryBarStore",
    "InMemoryMarketDataProvider",
    "Instrument",
    "InstrumentId",
    "InstrumentKind",
    "LedgerContext",
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
    "format_currency",
    "format_date_indian",
    "format_date_iso",
    "format_date_us",
    "format_eur",
    "format_inr",
    "format_usd",
    "indicators",
    "load_catalog",
    "register_event",
    "setup_event_logging",
]


def __getattr__(name: str) -> Any:
    """Lazy native-backed constants (SCHEMA_VERSION): they need the compiled extension."""
    from honba.wire import wire as _wire_module

    if name in {"SCHEMA_VERSION"}:
        return getattr(_wire_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
