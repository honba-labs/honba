"""Order intents emitted by strategies (mirrors honba-algo-strategies OrderIntent)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from honba.entities.instrument import InstrumentId


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TimeInForce(Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """A strategy's desire to trade, before it becomes a concrete order."""

    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY

    def __post_init__(self) -> None:
        if not self.quantity > 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.order_type is not OrderType.MARKET and self.price is None:
            raise ValueError(f"{self.order_type.value} order requires a price")

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
