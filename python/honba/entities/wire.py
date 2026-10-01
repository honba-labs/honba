"""Pydantic models of the canonical JSON wire contract (ADR 006).

The Rust serde types in ``honba-messages``, ``honba-entities`` and
``honba-strategy`` are the source of truth. These models mirror their JSON
form exactly and are verified against it by the shared golden vectors in
``schema/golden/`` and by a round trip through ``honba._honba.canonical_json``.

The strategy-facing dataclasses in ``honba.entities`` remain the ergonomic
API; ``from_domain`` / ``to_domain`` convert where the mapping is lossless.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, Strict, model_validator

from honba.entities import instrument as _instrument
from honba.entities import order as _order
from honba.entities.order import OrderSide, OrderStatus, OrderType, TimeInForce

SCHEMA_VERSION: Final[int] = 1
"""Wire-contract version; must equal ``honba_messages::SCHEMA_VERSION``."""

_U64_MAX = 2**64 - 1

UnixNanos = Annotated[int, Strict(), Field(ge=0, le=_U64_MAX)]
"""Nanoseconds since the Unix epoch, as a JSON integer (u64)."""
Float = Annotated[float, Strict()]
"""A finite f64 (ints are accepted and widened, strings are not)."""
Str = Annotated[str, Strict()]


def _canonical(enum: type[Enum]) -> BeforeValidator:
    """Accept only the canonical wire value, not aliases resolved by ``_missing_``."""
    values = {member.value for member in enum}

    def check(value: Any) -> Any:
        if isinstance(value, str) and value not in values:
            raise ValueError(f"{value!r} is not a valid {enum.__name__}")
        return value

    return BeforeValidator(check)


class Currency(Enum):
    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


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


class AggressorSide(Enum):
    BUYER = "buyer"
    SELLER = "seller"
    NO_AGGRESSOR = "no_aggressor"


WireOrderSide = Annotated[OrderSide, _canonical(OrderSide)]
WireOrderType = Annotated[OrderType, _canonical(OrderType)]
WireTimeInForce = Annotated[TimeInForce, _canonical(TimeInForce)]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class InstrumentId(_Wire):
    symbol: Str
    venue: Str

    @classmethod
    def from_domain(cls, value: _instrument.InstrumentId) -> InstrumentId:
        return cls(symbol=value.symbol, venue=value.venue)

    def to_domain(self) -> _instrument.InstrumentId:
        return _instrument.InstrumentId(self.symbol, self.venue)


class BarSpecification(_Wire):
    step: Annotated[int, Strict(), Field(ge=0, le=_U64_MAX)]
    aggregation: BarAggregation
    price_type: PriceType


class BarType(_Wire):
    instrument_id: InstrumentId
    spec: BarSpecification


class Bar(_Wire):
    bar_type: BarType
    open: Float
    high: Float
    low: Float
    close: Float
    volume: Float
    ts_event: UnixNanos
    ts_init: UnixNanos


class QuoteTick(_Wire):
    instrument_id: InstrumentId
    bid_price: Float
    ask_price: Float
    bid_size: Float
    ask_size: Float
    ts_event: UnixNanos
    ts_init: UnixNanos


class TradeTick(_Wire):
    instrument_id: InstrumentId
    price: Float
    size: Float
    aggressor_side: AggressorSide
    trade_id: Str
    ts_event: UnixNanos
    ts_init: UnixNanos


class Order(_Wire):
    order_id: Str
    instrument_id: InstrumentId
    side: WireOrderSide
    order_type: WireOrderType
    quantity: Float
    price: Float | None = None
    trigger_price: Float | None = None
    status: OrderStatus
    time_in_force: WireTimeInForce
    ts_event: UnixNanos
    ts_init: UnixNanos


class OrderIntent(_Wire):
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
    order_id: Str
    instrument_id: InstrumentId
    side: WireOrderSide
    quantity: Float
    price: Float
    costs: Float
    ts_event: UnixNanos
    ts_init: UnixNanos


class Position(_Wire):
    instrument_id: InstrumentId
    currency: Currency
    side: PositionSide
    quantity: Float
    avg_price: Float
    realized_pnl: Float


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
    last_qty: Float
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
        return cls(schema_version=SCHEMA_VERSION, event=event, ts_init=ts_init)


MODELS: Final[dict[str, Any]] = {
    "InstrumentId": InstrumentId,
    "Bar": Bar,
    "Order": Order,
    "OrderIntent": OrderIntent,
    "Trade": Trade,
    "Position": Position,
    "Event": Event,
    "Message": Message,
}
"""Wire-contract type name (as in the golden files and ``canonical_json``) to model."""
