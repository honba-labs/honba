"""Executed fill (mirrors honba_entities::Trade)."""

from __future__ import annotations

from dataclasses import dataclass, field

from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money
from honba.domain.order import OrderSide


@dataclass(frozen=True, slots=True)
class Trade:
    """A fill. ``costs`` is the total transaction cost as integer ``Money`` (ADR 0011).

    A plain number for ``costs`` is the legacy major-unit form: it is converted
    once, here, to INR minor units (half away from zero), like the Rust reader.
    """

    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    price: float
    ts: int = 0
    order_id: str | None = None
    costs: Money = field(default_factory=lambda: Money.zero(Currency.INR))

    def __post_init__(self) -> None:
        costs = self.costs
        if isinstance(costs, (int, float)) and not isinstance(costs, bool):
            object.__setattr__(self, "costs", Money.from_major(costs, Currency.INR))
        elif not isinstance(costs, Money):
            raise TypeError(f"costs must be Money, got {type(costs).__name__}")
