"""Screener and metric definition models for Honba (ADR 006 / Pillar P2)."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
)

from honba.entities.wire import _canonical, _Wire, Str


class ValueType(Enum):
    NUMBER = "NUMBER"
    STRING = "STRING"
    ENUM = "ENUM"
    BOOL = "BOOL"
    DATE = "DATE"
    MONEY = "MONEY"


class UnitType(Enum):
    PCT = "PCT"
    PRICE = "PRICE"
    RATIO = "RATIO"
    SHARES = "SHARES"
    CURRENCY = "CURRENCY"


class MetricPeriod(Enum):
    SNAPSHOT = "SNAPSHOT"
    TTM = "TTM"
    FY = "FY"
    FQ = "FQ"
    H1 = "H1"
    CURRENT = "CURRENT"


class Timeframe(Enum):
    M1 = "1"
    M5 = "5"
    M15 = "15"
    M30 = "30"
    H1 = "60"
    H2 = "120"
    H4 = "240"
    D1 = "1D"
    W1 = "1W"
    MONTH1 = "1M"


class FilterOp(Enum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    LIKE = "like"
    HAS = "has"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


WireValueType = Annotated[ValueType, _canonical(ValueType)]
WireUnitType = Annotated[UnitType, _canonical(UnitType)]
WireMetricPeriod = Annotated[MetricPeriod, _canonical(MetricPeriod)]
WireTimeframe = Annotated[Timeframe, _canonical(Timeframe)]
WireFilterOp = Annotated[FilterOp, _canonical(FilterOp)]


class MetricKeySpec(_Wire):
    """Specification of a metric with optional period and timeframe dimensions."""

    key: Str
    period: WireMetricPeriod | None = None
    timeframe: WireTimeframe | None = None


class MetricDefinition(_Wire):
    """Catalog definition of a metric."""

    key: Str
    label: Str
    group: Str
    value_type: WireValueType = Field(alias="valueType")
    unit: WireUnitType | None = None
    description: Str | None = None
    has_period: Annotated[bool, Strict()] = Field(default=False, alias="hasPeriod")
    has_timeframe: Annotated[bool, Strict()] = Field(default=False, alias="hasTimeframe")
    default_period: Str | None = Field(default=None, alias="defaultPeriod")
    default_timeframe: Str | None = Field(default=None, alias="defaultTimeframe")
    filterable: Annotated[bool, Strict()] = True
    sortable: Annotated[bool, Strict()] = True
    source: Str | None = None


class ScreenerFilterPredicate(_Wire):
    """Filter predicate for screening instruments."""

    key: Str
    op: WireFilterOp
    value: Any
    period: WireMetricPeriod | None = None
    timeframe: WireTimeframe | None = None


class ScreenerFilterGroup(_Wire):
    """Logical group of filter predicates."""

    operator: Literal["AND", "OR"] = "AND"
    items: list[Any]


class ScreenerSortSpec(_Wire):
    """Sorting specification for screener results."""

    key: Str
    dir: Literal["asc", "desc"] = "desc"
    period: WireMetricPeriod | None = None
    timeframe: WireTimeframe | None = None


class ScreenerScanRequest(_Wire):
    """Request payload for POST /api/v1/screener/scan."""

    market: Str
    types: list[Str] = Field(default_factory=lambda: ["EQUITY"])
    primary_only: Annotated[bool, Strict()] = Field(default=True, alias="primaryOnly")
    column_set: Str | None = Field(default=None, alias="columnSet")
    columns: list[MetricKeySpec] = Field(default_factory=list)
    filters: ScreenerFilterGroup | None = None
    sort: ScreenerSortSpec | None = None
    range: tuple[int, int] = (0, 50)


class ScreenerRow(_Wire):
    """A row of screened instrument results."""

    full_symbol: Str = Field(alias="fullSymbol")
    instrument_id: Str = Field(alias="instrumentId")
    name: Str
    values: dict[str, Any]


class ScreenerScanResponse(_Wire):
    """Response payload for POST /api/v1/screener/scan."""

    total: Annotated[int, Strict()]
    range: tuple[int, int]
    columns: list[Str]
    rows: list[ScreenerRow]
