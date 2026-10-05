"""Pydantic models of the canonical JSON wire contract (ADR 006).

The Rust serde types in ``honba-messages``, ``honba-entities`` and
``honba-strategy`` are the source of truth. These models mirror their JSON
form exactly and are verified against it by the shared golden vectors in
``schema/golden/`` and by a round trip through ``honba._honba.canonical_json``.

The strategy-facing dataclasses in ``honba.entities`` remain the ergonomic
API; ``from_domain`` / ``to_domain`` convert where the mapping is lossless.
"""

from __future__ import annotations

import datetime as _dt
import json
from enum import Enum
from typing import Annotated, Any, Final, Literal

from pydantic import (
    Field,
    Strict,
    TypeAdapter,
    field_validator,
    model_validator,
)

from honba import _honba as _native
from honba.domain import instrument as _instrument
from honba.domain import money as _money
from honba.domain import order as _order
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce
from honba.domain.tick import AggressorSide
from honba.wire.base import Str, _canonical, _Command, _Wire
from honba.wire.screener import ScreenerFilterPredicate

SCHEMA_VERSION: Final[int] = _native.SCHEMA_VERSION
"""Wire-contract version, read from its one owner ``honba_messages::SCHEMA_VERSION`` (ADR 0012)."""

API_VERSION: Final[str] = _native.API_VERSION
"""API surface version, read from its one owner ``honba_messages::API_VERSION`` (ADR 0012)."""

_U64_MAX = 2**64 - 1


_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


class UnixNanos(_Wire):
    """Nanosecond timestamp with ISO-8601 string and unix_nanos string fields.

    Crosses JSON as an object (never a raw number, which exceeds
    Number.MAX_SAFE_INTEGER in JS/TS consumers). Like the Rust reader, the
    value is ``unix_nanos`` (a decimal ``u64``); ``iso`` is informational.
    """

    iso: Str
    unix_nanos: Str

    @field_validator("unix_nanos")
    @classmethod
    def _check_u64(cls, value: str) -> str:
        if not value.isascii() or not value.isdigit() or int(value) > _U64_MAX:
            raise ValueError(f"unix_nanos must be a decimal u64, got {value!r}")
        return value

    @classmethod
    def from_ns(cls, ns: int) -> UnixNanos:
        """The wire form of ``ns``, with the same ISO string Rust emits."""
        if isinstance(ns, bool) or not isinstance(ns, int) or not 0 <= ns <= _U64_MAX:
            raise ValueError(f"timestamp must be a u64 of nanoseconds, got {ns!r}")
        secs, nanos = divmod(ns, 1_000_000_000)
        stamp = (_EPOCH + _dt.timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%S")
        return cls(iso=f"{stamp}.{nanos:09d}Z", unix_nanos=str(ns))

    def to_ns(self) -> int:
        """Nanoseconds since the Unix epoch."""
        return int(self.unix_nanos)


Float = Annotated[float, Strict()]
"""A finite f64 (ints are accepted and widened, strings are not)."""
PositiveFloat = Annotated[float, Strict(), Field(gt=0)]
"""A finite f64 that must be > 0."""
NonNegativeFloat = Annotated[float, Strict(), Field(ge=0)]
"""A finite f64 that must be >= 0."""


Currency = _money.Currency
"""The settlement currency: the same enum as ``honba.domain.money.Currency``."""


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


class Money(_Wire):
    """Monetary amount in integer minor units (paise/cents)."""

    amount: Annotated[int, Strict(), Field(ge=-(2**63) + 1, le=2**63 - 1)]
    currency: Currency

    @field_validator("amount", mode="before")
    @classmethod
    def _legacy_major(cls, value: Any) -> Any:
        # Older producers wrote major-unit floats; round once, at the door
        # (half away from zero, like the Rust reader). Integers pass through.
        if isinstance(value, float):
            return _money.Money.from_major(value, _money.Currency.INR).amount
        return value

    @classmethod
    def from_domain(cls, value: _money.Money) -> Money:
        return cls(amount=value.amount, currency=value.currency)

    def to_domain(self) -> _money.Money:
        return _money.Money(self.amount, self.currency)


class BarAggregation(Enum):
    TICK = "tick"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class PriceType(Enum):
    BID = "bid"
    ASK = "ask"
    MID = "mid"
    LAST = "last"


WireOrderSide = Annotated[OrderSide, _canonical(OrderSide)]
WireOrderType = Annotated[OrderType, _canonical(OrderType)]
WireTimeInForce = Annotated[TimeInForce, _canonical(TimeInForce)]


class InstrumentId(_Wire):
    symbol: Str
    exchange: Str

    @classmethod
    def from_domain(cls, value: _instrument.InstrumentId) -> InstrumentId:
        return cls(symbol=value.symbol, exchange=value.exchange)

    def to_domain(self) -> _instrument.InstrumentId:
        return _instrument.InstrumentId(self.symbol, self.exchange)


class BarSpecification(_Wire):
    step: Annotated[int, Strict(), Field(ge=1, le=_U64_MAX)]
    aggregation: BarAggregation
    price_type: PriceType


class BarType(_Wire):
    instrument_id: InstrumentId
    spec: BarSpecification


class Bar(_Wire):
    """OHLCV bar; ``low <= open, close <= high`` and ``volume >= 0``."""

    bar_type: BarType
    open: Float
    high: Float
    low: Float
    close: Float
    volume: NonNegativeFloat
    ts_event: UnixNanos
    ts_init: UnixNanos

    @model_validator(mode="after")
    def _check_range(self) -> Bar:
        if self.low > self.high:
            raise ValueError("low must be <= high")
        for name in ("open", "close"):
            if not self.low <= getattr(self, name) <= self.high:
                raise ValueError(f"{name} must lie within [low, high]")
        return self


class QuoteTick(_Wire):
    """Top-of-book quote; ``bid_price <= ask_price`` and sizes ``>= 0``."""

    instrument_id: InstrumentId
    bid_price: Float
    ask_price: Float
    bid_size: NonNegativeFloat
    ask_size: NonNegativeFloat
    ts_event: UnixNanos
    ts_init: UnixNanos

    @model_validator(mode="after")
    def _check_spread(self) -> QuoteTick:
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price must be <= ask_price")
        return self


class TradeTick(_Wire):
    instrument_id: InstrumentId
    price: Float
    size: NonNegativeFloat
    aggressor_side: AggressorSide
    trade_id: Str
    ts_event: UnixNanos
    ts_init: UnixNanos


class Order(_Wire):
    """A client order record; ``quantity > 0`` (``side`` may be ``no_order_side``)."""

    order_id: Str
    instrument_id: InstrumentId
    side: WireOrderSide
    order_type: WireOrderType
    quantity: PositiveFloat
    price: Float | None = None
    trigger_price: Float | None = None
    status: OrderStatus
    time_in_force: WireTimeInForce
    ts_event: UnixNanos
    ts_init: UnixNanos


class OrderIntent(_Command):
    instrument_id: InstrumentId
    side: WireOrderSide
    quantity: Float
    order_type: WireOrderType
    price: Float | None = None
    trigger_price: Float | None = None
    time_in_force: WireTimeInForce

    @model_validator(mode="after")
    def _check_invariants(self) -> OrderIntent:
        _order.validate_intent(
            self.side, self.quantity, self.order_type, self.price, self.trigger_price
        )
        return self

    @classmethod
    def from_domain(cls, value: _order.OrderIntent) -> OrderIntent:
        return cls(
            instrument_id=InstrumentId.from_domain(value.instrument_id),
            side=value.side,
            quantity=value.quantity,
            order_type=value.order_type,
            price=value.price,
            trigger_price=value.trigger_price,
            time_in_force=value.time_in_force,
        )

    def to_domain(self) -> _order.OrderIntent:
        return _order.OrderIntent(
            self.instrument_id.to_domain(),
            self.side,
            self.quantity,
            self.order_type,
            self.price,
            self.time_in_force,
            self.trigger_price,
        )


class Trade(_Wire):
    """A fill; ``side`` is buy or sell, ``quantity > 0``, ``price > 0``."""

    order_id: Str
    instrument_id: InstrumentId
    side: WireOrderSide
    quantity: PositiveFloat
    price: PositiveFloat
    costs: Money
    ts_event: UnixNanos
    ts_init: UnixNanos

    @field_validator("costs", mode="before")
    @classmethod
    def _legacy_costs(cls, value: Any) -> Any:
        # Legacy journals wrote a bare major-unit float; it has no currency, so
        # INR is assumed (the Rust reader does the same).
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return {"amount": float(value), "currency": "INR"}
        return value

    @model_validator(mode="after")
    def _check_side(self) -> Trade:
        if self.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError("trade side must be buy or sell")
        return self


class Position(_Wire):
    """A position; ``quantity`` and ``avg_price`` are ``>= 0`` (``side`` gives direction)."""

    instrument_id: InstrumentId
    currency: Currency
    side: PositionSide
    quantity: NonNegativeFloat
    avg_price: NonNegativeFloat
    realized_pnl: Money

    @model_validator(mode="before")
    @classmethod
    def _legacy_realized_pnl(cls, data: Any) -> Any:
        # Legacy writers emitted a bare major-unit float; it takes the
        # position's currency (as in the Rust reader).
        if isinstance(data, dict):
            pnl = data.get("realized_pnl")
            if isinstance(pnl, (int, float)) and not isinstance(pnl, bool):
                currency = data.get("currency")
                return {**data, "realized_pnl": {"amount": float(pnl), "currency": currency}}
        return data


# Event variants: internally tagged by "type", like the Rust enum.


class QuoteEvent(QuoteTick):
    type: Literal["quote"] = "quote"


class TradeEvent(TradeTick):
    type: Literal["trade"] = "trade"


class BarEvent(Bar):
    type: Literal["bar"] = "bar"


class OrderEvent(Order):
    type: Literal["order"] = "order"


class OrderAccepted(_Wire):
    type: Literal["order_accepted"] = "order_accepted"
    order_id: Str
    ts_event: UnixNanos


class OrderRejected(_Wire):
    type: Literal["order_rejected"] = "order_rejected"
    order_id: Str
    reason: Str
    ts_event: UnixNanos


class OrderFilled(_Wire):
    type: Literal["order_filled"] = "order_filled"
    order_id: Str
    last_qty: PositiveFloat
    last_px: Float
    ts_event: UnixNanos


class OrderCancelled(_Wire):
    type: Literal["order_cancelled"] = "order_cancelled"
    order_id: Str
    ts_event: UnixNanos


Event = Annotated[
    QuoteEvent
    | TradeEvent
    | BarEvent
    | OrderEvent
    | OrderAccepted
    | OrderRejected
    | OrderFilled
    | OrderCancelled,
    Field(discriminator="type"),
]
"""Any event, discriminated by its ``type`` field."""


class Message(_Wire):
    """The versioned envelope; ``schema_version`` must equal ``SCHEMA_VERSION``."""

    schema_version: Annotated[int, Strict()]
    event: Event
    ts_init: UnixNanos

    @model_validator(mode="after")
    def _check_version(self) -> Message:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {self.schema_version}; "
                f"this build reads {SCHEMA_VERSION}"
            )
        return self

    @classmethod
    def wrap(cls, event: Any, ts_init: int) -> Message:
        """Wrap an event in an envelope stamped with the current schema version."""
        return cls(schema_version=SCHEMA_VERSION, event=event, ts_init=UnixNanos.from_ns(ts_init))


MODELS: Final[dict[str, Any]] = {
    "InstrumentId": InstrumentId,
    "Bar": Bar,
    "Order": Order,
    "OrderIntent": OrderIntent,
    "Trade": Trade,
    "Position": Position,
    "Event": Event,
    "Message": Message,
    "ScreenerFilterPredicate": ScreenerFilterPredicate,
}
"""Wire-contract type name (as in the golden files and ``canonical_json``) to model."""

ENUMS: Final[dict[str, type[Enum]]] = {
    "OrderSide": OrderSide,
    "OrderType": OrderType,
    "OrderStatus": OrderStatus,
    "TimeInForce": TimeInForce,
    "BarAggregation": BarAggregation,
    "PriceType": PriceType,
    "AggressorSide": AggressorSide,
    "PositionSide": PositionSide,
    "Currency": Currency,
}
"""Wire enum name (as in ``honba._honba.wire_enum_values``) to its Python enum.

Aliases (``OrderType.STOP``) are not separate members, so iterating an enum
yields exactly its canonical wire values.
"""

_ADAPTERS: Final[dict[str, TypeAdapter[Any]]] = {
    name: TypeAdapter(model) for name, model in MODELS.items()
}


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid wire JSON (numbers must be finite)")


def loads(kind: str, data: str | bytes) -> Any:
    """Parse wire JSON text as the model for ``kind`` (a key of ``MODELS``).

    This is the strict parse path for payloads from other processes. Unlike
    ``TypeAdapter.validate_json`` (which keeps the last of duplicated keys) it
    rejects duplicate object keys at any depth, as Rust's serde does, and the
    non-standard ``NaN`` / ``Infinity`` literals. Raises ``ValueError``
    (``pydantic.ValidationError`` for schema violations).
    """
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        raise ValueError(f"unknown wire kind {kind!r}; expected one of {sorted(MODELS)}")
    obj = json.loads(data, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constant)
    return adapter.validate_python(obj)


def loads_many(kind: str, data: str | bytes) -> list[Any]:
    """Parse a JSON array of ``kind`` values with the same strict rules as ``loads``."""
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        raise ValueError(f"unknown wire kind {kind!r}; expected one of {sorted(MODELS)}")
    items = json.loads(data, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constant)
    if not isinstance(items, list):
        raise TypeError(f"expected a JSON array of {kind}, got {type(items).__name__}")
    return [adapter.validate_python(item) for item in items]
