"""The ``StrategyContext`` port: a strategy's only view of the world (ADR 008).

Mirrors the Rust ``honba_strategy::StrategyContext`` trait. A strategy reads the
clock, its positions, cash and instrument metadata, and submits order intents
through the context. It never touches execution, I/O or the wall clock, so the
same strategy runs unchanged in backtest, paper and live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade

_EPSILON = 1e-9


class StrategyContext(ABC):
    """What a strategy may read and do. Implementations are supplied by the runner."""

    @abstractmethod
    def now(self) -> int:
        """``ts_init`` (unix ns) of the event being processed; 0 before the first event."""

    @abstractmethod
    def position(self, instrument_id: InstrumentId) -> float:
        """Net signed quantity held (positive long, negative short), updated from fills."""

    @abstractmethod
    def positions(self) -> dict[InstrumentId, float]:
        """Every non-flat position, ordered by instrument id (symbol, then venue)."""

    @abstractmethod
    def cash(self) -> float:
        """Initial cash plus the net cash flow of all fills (buys debit, sells credit, costs debit)."""

    @abstractmethod
    def busy(self, instrument_id: InstrumentId) -> bool:
        """True while an intent submitted for ``instrument_id`` is not fully filled or rejected."""

    @abstractmethod
    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Instrument metadata (lot and tick size), or ``None`` if the run does not know it."""

    @abstractmethod
    def submit(self, intent: OrderIntent) -> None:
        """Queue an intent; the runner turns it into an order after the current hook returns."""


class LedgerContext(StrategyContext):
    """The reference context (mirrors ``honba_strategy::LedgerContext``).

    A deterministic in-memory ledger: the runner sets the clock, applies fills and
    releases rejected intents; the strategy reads it and submits intents, which the
    runner drains. Pure: no I/O and no wall clock, so backtest and live share it.
    """

    def __init__(self, cash: float = 0.0, instruments: Iterable[Instrument] = ()) -> None:
        self._now = 0
        self._cash = float(cash)
        self._positions: dict[InstrumentId, float] = {}
        self._pending: dict[tuple[InstrumentId, OrderSide], float] = {}
        self._instruments: dict[InstrumentId, Instrument] = {}
        self._outbox: list[OrderIntent] = []
        for instrument in instruments:
            self.add_instrument(instrument)

    # -- StrategyContext ------------------------------------------------------
    def now(self) -> int:
        return self._now

    def position(self, instrument_id: InstrumentId) -> float:
        return self._positions.get(instrument_id, 0.0)

    def positions(self) -> dict[InstrumentId, float]:
        held = (item for item in self._positions.items() if item[1] != 0.0)
        return dict(sorted(held, key=lambda item: (item[0].symbol, item[0].venue)))

    def cash(self) -> float:
        return self._cash

    def busy(self, instrument_id: InstrumentId) -> bool:
        return any(q > 0 for (iid, _), q in self._pending.items() if iid == instrument_id)

    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        return self._instruments.get(instrument_id)

    def submit(self, intent: OrderIntent) -> None:
        key = (intent.instrument_id, intent.side)
        self._pending[key] = self._pending.get(key, 0.0) + intent.quantity
        self._outbox.append(intent)

    # -- runner side ----------------------------------------------------------
    def set_now(self, ts: int) -> None:
        """Set the clock to the ``ts_init`` of the event about to be processed."""
        self._now = ts

    def add_instrument(self, instrument: Instrument) -> None:
        self._instruments[instrument.instrument_id] = instrument

    def drain_intents(self) -> list[OrderIntent]:
        """Return and clear submitted intents, in submission order."""
        intents, self._outbox = self._outbox, []
        return intents

    def apply_fill(self, fill: Trade) -> None:
        """Book a fill: position, cash (``quantity * price`` and costs) and pending quantity."""
        if fill.side is OrderSide.BUY:
            self._positions[fill.instrument_id] = self.position(fill.instrument_id) + fill.quantity
            self._cash -= fill.quantity * fill.price + fill.costs
        else:
            self._positions[fill.instrument_id] = self.position(fill.instrument_id) - fill.quantity
            self._cash += fill.quantity * fill.price - fill.costs
        self._reduce_pending(fill.instrument_id, fill.side, fill.quantity)

    def release(self, intent: OrderIntent) -> None:
        """An intent was rejected or its order cancelled unfilled: it no longer counts as busy."""
        self._reduce_pending(intent.instrument_id, intent.side, intent.quantity)

    def _reduce_pending(
        self, instrument_id: InstrumentId, side: OrderSide, quantity: float
    ) -> None:
        key = (instrument_id, side)
        left = self._pending.get(key, 0.0) - quantity
        if left > _EPSILON:
            self._pending[key] = left
        else:
            self._pending.pop(key, None)
