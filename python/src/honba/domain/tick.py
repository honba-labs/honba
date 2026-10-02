"""Market-data ticks (mirror honba_messages::QuoteTick and TradeTick).

Like ``honba.entities.bar.Bar`` these are the strategy-facing shapes: ``ts`` is the
event time (``ts_event`` on the wire). Invariants match the Rust ``validate()``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from honba.domain.instrument import InstrumentId


class AggressorSide(Enum):
    """Which side initiated a market trade."""

    BUYER = "buyer"
    SELLER = "seller"
    NO_AGGRESSOR = "no_aggressor"


def _finite(**values: float) -> None:
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value}")


@dataclass(frozen=True, slots=True)
class QuoteTick:
    """Top of book; ``bid_price <= ask_price`` and sizes ``>= 0``."""

    instrument_id: InstrumentId
    ts: int  # unix nanoseconds
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float

    def __post_init__(self) -> None:
        _finite(
            bid_price=self.bid_price,
            ask_price=self.ask_price,
            bid_size=self.bid_size,
            ask_size=self.ask_size,
        )
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price must be <= ask_price")
        if self.bid_size < 0 or self.ask_size < 0:
            raise ValueError("sizes must be >= 0")

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0


@dataclass(frozen=True, slots=True)
class TradeTick:
    """A trade printed on the market (not the strategy's own fill, which is a ``Trade``)."""

    instrument_id: InstrumentId
    ts: int  # unix nanoseconds
    price: float
    size: float
    aggressor_side: AggressorSide
    trade_id: str

    def __post_init__(self) -> None:
        _finite(price=self.price, size=self.size)
        if self.size < 0:
            raise ValueError("size must be >= 0")
