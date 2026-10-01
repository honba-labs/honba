"""Order intents emitted by strategies (mirrors honba_strategy::OrderIntent)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from honba.entities.instrument import InstrumentId


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"
    NO_ORDER_SIDE = "no_order_side"  # wire value only; intents reject it


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    STOP = "stop_market"  # noqa: PIE796 - deliberate alias of STOP_MARKET for existing callers

    @classmethod
    def _missing_(cls, value: object) -> OrderType | None:
        return cls.STOP_MARKET if value == "stop" else None


class TimeInForce(Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    GTD = "gtd"


class OrderStatus(Enum):
    INITIALIZED = "initialized"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


# (needs limit price, needs trigger price) per order type; same table as Rust.
_PRICE_RULES: dict[OrderType, tuple[bool, bool]] = {
    OrderType.MARKET: (False, False),
    OrderType.LIMIT: (True, False),
    OrderType.STOP_MARKET: (False, True),
    OrderType.STOP_LIMIT: (True, True),
}


def validate_intent(
    side: OrderSide,
    quantity: float,
    order_type: OrderType,
    price: float | None,
    trigger_price: float | None,
) -> None:
    """Raise ``ValueError`` if the fields break an ``OrderIntent`` invariant.

    Mirrors ``OrderIntent::validate`` in Rust: quantity is finite and > 0, side
    is buy or sell, prices are finite, and the order type decides which of
    ``price`` (limit) and ``trigger_price`` (stop) must be present.
    """
    if not (math.isfinite(quantity) and quantity > 0):
        raise ValueError(f"quantity must be positive, got {quantity}")
    if side not in (OrderSide.BUY, OrderSide.SELL):
        raise ValueError("side must be buy or sell")
    if any(p is not None and not math.isfinite(p) for p in (price, trigger_price)):
        raise ValueError("prices must be finite")
    needs_price, needs_trigger = _PRICE_RULES[order_type]
    name = order_type.value
    if needs_price and price is None:
        raise ValueError(f"{name} order requires a price")
    if not needs_price and price is not None:
        raise ValueError(f"{name} order takes no price")
    if needs_trigger and trigger_price is None:
        raise ValueError(f"{name} order requires a trigger_price")
    if not needs_trigger and trigger_price is not None:
        raise ValueError(f"{name} order takes no trigger_price")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A strategy's desire to trade, before it becomes a concrete order.

    ``price`` is the limit price (limit, stop-limit); ``trigger_price`` is the
    stop trigger (stop-market, stop-limit).
    """

    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    trigger_price: float | None = None

    def __post_init__(self) -> None:
        validate_intent(self.side, self.quantity, self.order_type, self.price, self.trigger_price)

    @classmethod
    def market_buy(cls, instrument_id: InstrumentId, quantity: float) -> OrderIntent:
        return cls(instrument_id, OrderSide.BUY, quantity)

    @classmethod
    def market_sell(cls, instrument_id: InstrumentId, quantity: float) -> OrderIntent:
        return cls(instrument_id, OrderSide.SELL, quantity)

    @classmethod
    def limit_buy(cls, instrument_id: InstrumentId, quantity: float, price: float) -> OrderIntent:
        return cls(instrument_id, OrderSide.BUY, quantity, OrderType.LIMIT, price)

    @classmethod
    def limit_sell(cls, instrument_id: InstrumentId, quantity: float, price: float) -> OrderIntent:
        return cls(instrument_id, OrderSide.SELL, quantity, OrderType.LIMIT, price)

    @classmethod
    def stop_buy(
        cls, instrument_id: InstrumentId, quantity: float, trigger_price: float
    ) -> OrderIntent:
        return cls(
            instrument_id,
            OrderSide.BUY,
            quantity,
            OrderType.STOP_MARKET,
            trigger_price=trigger_price,
        )

    @classmethod
    def stop_sell(
        cls, instrument_id: InstrumentId, quantity: float, trigger_price: float
    ) -> OrderIntent:
        return cls(
            instrument_id,
            OrderSide.SELL,
            quantity,
            OrderType.STOP_MARKET,
            trigger_price=trigger_price,
        )

    @classmethod
    def stop_limit_buy(
        cls,
        instrument_id: InstrumentId,
        quantity: float,
        trigger_price: float,
        limit_price: float,
    ) -> OrderIntent:
        return cls(
            instrument_id,
            OrderSide.BUY,
            quantity,
            OrderType.STOP_LIMIT,
            limit_price,
            trigger_price=trigger_price,
        )

    @classmethod
    def stop_limit_sell(
        cls,
        instrument_id: InstrumentId,
        quantity: float,
        trigger_price: float,
        limit_price: float,
    ) -> OrderIntent:
        return cls(
            instrument_id,
            OrderSide.SELL,
            quantity,
            OrderType.STOP_LIMIT,
            limit_price,
            trigger_price=trigger_price,
        )
