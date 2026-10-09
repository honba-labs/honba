"""Order intents emitted by strategies (mirrors honba_strategy::OrderIntent).

Parity note: ``OrderType.TRAILING_STOP`` and the ``trail_amount`` / ``trail_percent`` fields
are Python-only for now. The Rust ``OrderType`` / ``OrderIntent`` do not have them yet
(parity pending), so a trailing stop must not be sent across the ``_honba`` boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from honba.domain.instrument import InstrumentId


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"
    NO_ORDER_SIDE = "no_order_side"  # wire value only; intents reject it


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    #: Trailing stop (market order once triggered). Opt-in per adapter: only valid where the
    #: adapter lists it in ``AdapterCapabilities.order_types``. Python-only; no Rust twin yet.
    TRAILING_STOP = "trailing_stop"
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
    OrderType.TRAILING_STOP: (False, False),
}


def validate_intent(
    side: OrderSide,
    quantity: float,
    order_type: OrderType,
    price: float | None,
    trigger_price: float | None,
    trail_amount: float | None = None,
    trail_percent: float | None = None,
) -> None:
    """Raise ``ValueError`` if the fields break an ``OrderIntent`` invariant.

    Mirrors ``OrderIntent::validate`` in Rust: quantity is finite and > 0, side
    is buy or sell, prices are finite, and the order type decides which of
    ``price`` (limit) and ``trigger_price`` (stop) must be present.

    Python-only extension (Rust parity pending): ``TRAILING_STOP`` takes no ``price`` and no
    ``trigger_price`` and exactly one of ``trail_amount`` (absolute price distance, finite
    and > 0) or ``trail_percent`` (0 < p < 100). Every other order type rejects both
    ``trail_*`` fields.
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
    _validate_trail(order_type, trail_amount, trail_percent)


def _validate_trail(
    order_type: OrderType, trail_amount: float | None, trail_percent: float | None
) -> None:
    name = order_type.value
    if order_type is not OrderType.TRAILING_STOP:
        if trail_amount is not None:
            raise ValueError(f"{name} order takes no trail_amount")
        if trail_percent is not None:
            raise ValueError(f"{name} order takes no trail_percent")
        return
    if (trail_amount is None) == (trail_percent is None):
        raise ValueError(f"{name} order requires exactly one of trail_amount or trail_percent")
    if trail_amount is not None and not (math.isfinite(trail_amount) and trail_amount > 0):
        raise ValueError(f"trail_amount must be finite and positive, got {trail_amount}")
    if trail_percent is not None and not (math.isfinite(trail_percent) and 0 < trail_percent < 100):
        raise ValueError(f"trail_percent must be in (0, 100), got {trail_percent}")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A strategy's desire to trade, before it becomes a concrete order.

    ``price`` is the limit price (limit, stop-limit); ``trigger_price`` is the
    stop trigger (stop-market, stop-limit). For ``TRAILING_STOP`` set exactly one of
    ``trail_amount`` (absolute price distance) or ``trail_percent`` (percent, 0 < p < 100)
    and leave ``price`` and ``trigger_price`` unset; build it with
    :meth:`trailing_stop_sell` / :meth:`trailing_stop_buy`. Only adapters listing
    ``OrderType.TRAILING_STOP`` in their capabilities accept it.
    """

    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    trigger_price: float | None = None
    trail_amount: float | None = None
    trail_percent: float | None = None

    def __post_init__(self) -> None:
        validate_intent(
            self.side,
            self.quantity,
            self.order_type,
            self.price,
            self.trigger_price,
            self.trail_amount,
            self.trail_percent,
        )

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

    @classmethod
    def trailing_stop_sell(
        cls,
        instrument_id: InstrumentId,
        quantity: float,
        *,
        trail_amount: float | None = None,
        trail_percent: float | None = None,
    ) -> OrderIntent:
        """Protect a LONG: SELL stop that trails below the high-water mark.

        The stop sits ``trail_amount`` (price units) or ``trail_percent`` percent below the
        highest price seen since placement; it rises with the market, never falls, and sells
        at market when price falls back to it. Give exactly one of the two trail arguments.
        Raises ``CapabilityError`` at ``place_order`` on adapters not listing TRAILING_STOP.
        """
        return cls(
            instrument_id,
            OrderSide.SELL,
            quantity,
            OrderType.TRAILING_STOP,
            trail_amount=trail_amount,
            trail_percent=trail_percent,
        )

    @classmethod
    def trailing_stop_buy(
        cls,
        instrument_id: InstrumentId,
        quantity: float,
        *,
        trail_amount: float | None = None,
        trail_percent: float | None = None,
    ) -> OrderIntent:
        """Protect a SHORT: BUY stop that trails above the low-water mark.

        The stop sits ``trail_amount`` (price units) or ``trail_percent`` percent above the
        lowest price seen since placement; it falls with the market, never rises, and buys
        at market when price rallies back to it. Give exactly one of the two trail arguments.
        Raises ``CapabilityError`` at ``place_order`` on adapters not listing TRAILING_STOP.
        """
        return cls(
            instrument_id,
            OrderSide.BUY,
            quantity,
            OrderType.TRAILING_STOP,
            trail_amount=trail_amount,
            trail_percent=trail_percent,
        )
