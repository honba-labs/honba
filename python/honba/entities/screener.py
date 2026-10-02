"""Screener and metric definition models for Honba (ADR 006 / Pillar P2)."""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    Strict,
    model_serializer,
    model_validator,
)

from honba.entities._wire_base import Str, _canonical, _Wire


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


def _omit_absent_dimensions(data: Any) -> Any:
    """Drop ``period`` / ``timeframe`` when unset, as Rust's ``skip_serializing_if`` does."""
    if isinstance(data, dict):
        for name in ("period", "timeframe"):
            if name in data and data[name] is None:
                del data[name]
    return data


class MetricRef(_Wire):
    """A metric used as the right-hand operand of a predicate.

    Makes metric-to-metric comparisons expressible on the wire, e.g.
    ``SMA50 crosses_above {"key": "SMA200"}``. ``key`` is the wire key, never a UI id.
    """

    key: Str
    period: WireMetricPeriod | None = None
    timeframe: WireTimeframe | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> Any:
        return _omit_absent_dimensions(handler(self))


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


_ORDERING_OPS = frozenset({FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE})
_CROSSING_OPS = frozenset({FilterOp.CROSSES_ABOVE, FilterOp.CROSSES_BELOW})
_SET_OPS = frozenset({FilterOp.IN, FilterOp.NOT_IN})


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _metric_ref(value: Any, op: FilterOp) -> MetricRef:
    if isinstance(value, MetricRef):
        return value
    if isinstance(value, dict):
        return MetricRef.model_validate(value)
    raise ValueError(f"{op.value}: value must be a finite number or a MetricRef object")


def _check_value(op: FilterOp, value: Any) -> Any:
    """Enforce the per-operator value contract (mirrors Rust ``check_predicate_value``).

    - ``crosses_above`` / ``crosses_below``: a finite number or a ``MetricRef``.
    - ``gt`` / ``gte`` / ``lt`` / ``lte``: a finite number, a string, or a ``MetricRef``.
    - ``between``: a list of exactly two finite numbers.
    - ``in`` / ``not_in``: a list.
    - other operators: unconstrained.
    """
    if op in _CROSSING_OPS:
        return value if _is_finite_number(value) else _metric_ref(value, op)
    if op in _ORDERING_OPS:
        if _is_finite_number(value) or isinstance(value, str):
            return value
        return _metric_ref(value, op)
    if op is FilterOp.BETWEEN:
        if not (isinstance(value, list) and len(value) == 2 and all(map(_is_finite_number, value))):
            raise ValueError("between: value must be a list of two finite numbers")
        return value
    if op in _SET_OPS:
        if not isinstance(value, list):
            raise ValueError(f"{op.value}: value must be a list")
        return value
    return value


class ScreenerFilterPredicate(_Wire):
    """Filter predicate for screening instruments.

    ``value`` must fit ``op``: ``crosses_above`` / ``crosses_below`` take a finite number
    or a ``MetricRef``; ``gt`` / ``gte`` / ``lt`` / ``lte`` a finite number, a string or a
    ``MetricRef``; ``between`` a list of two finite numbers; ``in`` / ``not_in`` a list;
    other operators any value. A ``MetricRef`` operand is parsed into a ``MetricRef``.
    """

    key: Str
    op: WireFilterOp
    value: Any
    period: WireMetricPeriod | None = None
    timeframe: WireTimeframe | None = None

    @model_validator(mode="after")
    def _value_matches_op(self) -> ScreenerFilterPredicate:
        checked = _check_value(self.op, self.value)
        if checked is not self.value:
            # Frozen model: replace the raw dict with the parsed MetricRef.
            object.__setattr__(self, "value", checked)
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> Any:
        return _omit_absent_dimensions(handler(self))


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
