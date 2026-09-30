"""The Strategy interface.

Mirrors the Rust ``Strategy`` trait: strategies receive typed callbacks and
accumulate order intents; they never touch execution directly, so the same
strategy runs in backtest, paper and live.
"""
from __future__ import annotations

from typing import ClassVar

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade


class Strategy:
    """Subclass, set ``name``, override the hooks you need."""

    name: ClassVar[str]

    def __new__(cls, *args, **kwargs):
        if not getattr(cls, "name", None):
            raise TypeError(f"{cls.__name__} must define a class attribute `name`")
        self = super().__new__(cls)
        self._intents: list[OrderIntent] = []
        self._positions: dict[InstrumentId, float] = {}
        return self

    # -- hooks (all default to no-ops) --------------------------------------
    def on_start(self) -> None: ...

    def on_bar(self, bar: Bar) -> None: ...

    def on_fill(self, fill: Trade) -> None: ...

    def on_stop(self) -> None: ...

    # -- helpers for subclasses ---------------------------------------------
    def position(self, instrument_id: InstrumentId) -> float:
        """Net signed quantity held, updated from fills."""
        return self._positions.get(instrument_id, 0.0)

    def buy(self, instrument_id: InstrumentId, quantity: float) -> None:
        self._intents.append(OrderIntent.market_buy(instrument_id, quantity))

    def sell(self, instrument_id: InstrumentId, quantity: float) -> None:
        self._intents.append(OrderIntent.market_sell(instrument_id, quantity))

    def submit(self, intent: OrderIntent) -> None:
        self._intents.append(intent)

    # -- runner interface ---------------------------------------------------
    def drain_intents(self) -> list[OrderIntent]:
        """Returns and clears pending intents. The runner calls this after every event."""
        intents, self._intents = self._intents, []
        return intents

    def handle_fill(self, fill: Trade) -> None:
        """Runner entry point: updates position, then calls ``on_fill``."""
        sign = 1.0 if fill.side is OrderSide.BUY else -1.0
        self._positions[fill.instrument_id] = self.position(fill.instrument_id) + sign * fill.quantity
        self.on_fill(fill)
