"""The Strategy interface.

Mirrors the Rust ``Strategy`` trait: strategies receive typed callbacks and
accumulate order intents; they never touch execution directly, so the same
strategy runs in backtest, paper and live.
"""
from __future__ import annotations

from abc import ABC
from typing import ClassVar

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade


class Strategy(ABC):
    """Subclass, set ``name``, override the hooks you need (ADR 008)."""

    name: ClassVar[str]

    def __new__(cls, *args, **kwargs):
        if not getattr(cls, "name", None):
            raise TypeError(f"{cls.__name__} must define a class attribute `name`")
        self = super().__new__(cls)
        self._intents: list[OrderIntent] = []
        self._positions: dict[InstrumentId, float] = {}
        self._pending: dict[tuple[InstrumentId, OrderSide], float] = {}
        return self

    # -- hooks (all default to no-ops) --------------------------------------
    def on_start(self) -> None: ...

    def on_bar(self, bar: Bar) -> None: ...

    def on_quote(self, quote: QuoteTick) -> None:
        """Top-of-book update."""

    def on_trade(self, trade: TradeTick) -> None:
        """A market trade print (the strategy's own executions arrive in ``on_fill``)."""

    def on_fill(self, fill: Trade) -> None: ...

    def on_stop(self) -> None: ...

    # -- helpers for subclasses ---------------------------------------------
    def position(self, instrument_id: InstrumentId) -> float:
        """Net signed quantity held, updated from fills."""
        return self._positions.get(instrument_id, 0.0)

    def busy(self, instrument_id: InstrumentId) -> bool:
        """True while an order for this instrument is unfilled.

        Fills arrive after the strategy emits an intent, so gate new orders on
        this to avoid duplicate entries or exits.
        """
        return any(q > 0 for (iid, _), q in self._pending.items() if iid == instrument_id)

    def buy(self, instrument_id: InstrumentId, quantity: float) -> None:
        self.submit(OrderIntent.market_buy(instrument_id, quantity))

    def sell(self, instrument_id: InstrumentId, quantity: float) -> None:
        self.submit(OrderIntent.market_sell(instrument_id, quantity))

    def submit(self, intent: OrderIntent) -> None:
        key = (intent.instrument_id, intent.side)
        self._pending[key] = self._pending.get(key, 0.0) + intent.quantity
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
        self._release(fill.instrument_id, fill.side, fill.quantity)
        self.on_fill(fill)

    def handle_rejected(self, intent: OrderIntent) -> None:
        """Runner entry point: an order was rejected or cancelled unfilled."""
        self._release(intent.instrument_id, intent.side, intent.quantity)

    def _release(self, instrument_id: InstrumentId, side: OrderSide, quantity: float) -> None:
        key = (instrument_id, side)
        left = self._pending.get(key, 0.0) - quantity
        if left > 1e-9:
            self._pending[key] = left
        else:
            self._pending.pop(key, None)
