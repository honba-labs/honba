"""Screener services: the metric catalog and natural-language metric resolution."""

from __future__ import annotations

from honba.screener.catalog import (
    CATALOG_ENV_VAR,
    AmbiguousMetric,
    CatalogError,
    MetricCatalog,
    MetricResolutionError,
    UnknownMetric,
    default_catalog_path,
    load_catalog,
    normalize_phrase,
)
from honba.screener.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
    merge_close_intervals,
    merge_intervals,
    plan_gaps,
    subtract_intervals,
)
from honba.screener.ports import (
    BarStore,
    InMemoryBarStore,
    InMemoryMarketDataProvider,
    MarketDataProvider,
    ScreenerSource,
    validate_bar,
)
from honba.screener.service import (
    DataEnsureResult,
    DataService,
    GapFetchPlan,
    MissingDataPolicy,
    OnMissingAction,
)

__all__ = [
    "CATALOG_ENV_VAR",
    "AmbiguousMetric",
    "BarStore",
    "CatalogError",
    "CoverageRecord",
    "CoverageStatus",
    "DataEnsureResult",
    "DataService",
    "DateInterval",
    "GapFetchPlan",
    "InMemoryBarStore",
    "InMemoryMarketDataProvider",
    "MarketDataProvider",
    "MetricCatalog",
    "MetricResolutionError",
    "MissingDataPolicy",
    "OnMissingAction",
    "ScreenerSource",
    "UnknownMetric",
    "default_catalog_path",
    "load_catalog",
    "merge_close_intervals",
    "merge_intervals",
    "normalize_phrase",
    "plan_gaps",
    "subtract_intervals",
    "validate_bar",
]
