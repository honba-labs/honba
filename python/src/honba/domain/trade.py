"""Executed fill (mirrors honba_entities::Trade)."""

from __future__ import annotations

from dataclasses import dataclass

from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide


@dataclass(frozen=True, slots=True)
class Trade:
    """A fill. ``costs`` is the total transaction cost in settlement currency."""

    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    price: float
    ts: int = 0
    order_id: str | None = None
    costs: float = 0.0
