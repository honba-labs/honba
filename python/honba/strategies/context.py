"""The ``StrategyContext`` port: a strategy's only view of the world (ADR 008).

Mirrors the Rust ``honba_strategy::StrategyContext`` trait. A strategy reads the
clock, its positions, cash and instrument metadata, and submits order intents
through the context. It never touches execution, I/O or the wall clock, so the
same strategy runs unchanged in backtest, paper and live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent


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
