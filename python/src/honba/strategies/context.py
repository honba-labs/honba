"""The ``StrategyContext`` port: a strategy's only view of the world (ADR 008).

Mirrors the Rust ``honba_strategy::StrategyContext`` trait. A strategy reads the
clock, its positions, cash and instrument metadata, and submits order intents
through the context. It never touches execution, I/O or the wall clock, so the
same strategy runs unchanged in backtest, paper and live.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable

from honba.domain.money import Currency, Money
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent, OrderSide, validate_intent
from honba.entities.trade import Trade

_EPSILON = 1e-9


def _is_valid(intent: OrderIntent) -> bool:
    try:
        validate_intent(
            intent.side, intent.quantity, intent.order_type, intent.price, intent.trigger_price
        )
    except ValueError:
        return False
    return True


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
        """Every non-flat position, ordered by instrument id (symbol, then exchange)."""

    @abstractmethod
    def cash(self) -> Money:
        """Initial cash plus the net cash flow of all fills (buys debit notional plus costs,
        sells credit notional minus costs), as integer minor-unit ``Money`` (ADR 0011).

        Use ``cash().to_major()`` for a float to display or size with."""

    @abstractmethod
    def busy(self, instrument_id: InstrumentId) -> bool:
        """True while an intent submitted for ``instrument_id`` is not fully filled or rejected."""

    @abstractmethod
    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        """Instrument metadata (lot and tick size), or ``None`` if the run does not know it."""

    @abstractmethod
    def submit(self, intent: OrderIntent) -> None:
        """Queue an intent; the runner turns it into an order after the current hook returns."""

    # -- optional order management (concrete defaults keep older contexts valid) -----
    def cancel(self, intent: OrderIntent) -> None:
        """Ask for ``intent``'s order to be cancelled. Default: not supported, does nothing.

        Idempotent: an intent that was never submitted, or whose order already finished, is
        ignored. One still waiting to be sent is withdrawn. The runner delivers the request
        to the execution port once the current hook returns.
        """

    def intent_of(self, order_id: str | None) -> OrderIntent | None:
        """The intent the runner sent as ``order_id``, or ``None`` (unknown or unsupported)."""
        return None

    def is_open(self, intent: OrderIntent) -> bool:
        """Whether ``intent`` may still fill: waiting to be sent or working, not finished.

        Default for contexts that cannot tell: ``True``.
        """
        return True

    def pending_quantity(self, instrument_id: InstrumentId) -> float:
        """Quantity submitted for ``instrument_id`` and not yet filled or released.

        Default for contexts that cannot tell: unbounded while ``busy``, else zero.
        """
        return math.inf if self.busy(instrument_id) else 0.0


class LedgerContext(StrategyContext):
    """The reference context (mirrors ``honba_strategy::LedgerContext``).

    A deterministic in-memory ledger: the runner sets the clock, applies fills and
    releases rejected intents; the strategy reads it and submits intents, which the
    runner drains. Pure: no I/O and no wall clock, so backtest and live share it.

    Cash is integer ``Money`` (ADR 0011). ``cash`` may be ``Money`` or a major-unit
    number, converted once here (half away from zero; non-finite raises ``ValueError``).
    """

    def __init__(
        self,
        cash: Money | float = 0.0,
        instruments: Iterable[Instrument] = (),
        currency: Currency = Currency.INR,
    ) -> None:
        self._now = 0
        self._cash = cash if isinstance(cash, Money) else Money.from_major(cash, currency)
        self._positions: dict[InstrumentId, float] = {}
        self._pending: dict[tuple[InstrumentId, OrderSide], float] = {}
        self._instruments: dict[InstrumentId, Instrument] = {}
        self._outbox: list[OrderIntent] = []
        self._order_intents: dict[str, OrderIntent] = {}
        self._intent_orders: dict[int, str] = {}  # id(intent) -> order id (intents are kept alive)
        self._closed: set[str] = set()
        self._cancel_asked: set[str] = set()
        self._cancels: list[str] = []
        for instrument in instruments:
            self.add_instrument(instrument)

    # -- StrategyContext ------------------------------------------------------
    def now(self) -> int:
        return self._now

    def position(self, instrument_id: InstrumentId) -> float:
        return self._positions.get(instrument_id, 0.0)

    def positions(self) -> dict[InstrumentId, float]:
        held = (item for item in self._positions.items() if item[1] != 0.0)
        return dict(sorted(held, key=lambda item: (item[0].symbol, item[0].exchange)))

    def cash(self) -> Money:
        return self._cash

    @property
    def currency(self) -> Currency:
        """The currency this ledger settles in."""
        return self._cash.currency

    def busy(self, instrument_id: InstrumentId) -> bool:
        return any(q > 0 for (iid, _), q in self._pending.items() if iid == instrument_id)

    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        return self._instruments.get(instrument_id)

    def submit(self, intent: OrderIntent) -> None:
        # An invalid intent (only possible if built around ``OrderIntent``'s validation) is still
        # queued so the runner can reject it, but it must not touch the pending ledger: a NaN
        # quantity would wipe the other pending orders (same rule as the Rust ``LedgerContext``).
        if _is_valid(intent):
            key = (intent.instrument_id, intent.side)
            self._pending[key] = self._pending.get(key, 0.0) + intent.quantity
        self._outbox.append(intent)

    def pending_quantity(self, instrument_id: InstrumentId) -> float:
        return sum(q for (iid, _), q in self._pending.items() if iid == instrument_id)

    def is_open(self, intent: OrderIntent) -> bool:
        order_id = self._intent_orders.get(id(intent))
        if order_id is None:
            return any(queued is intent for queued in self._outbox)
        return order_id not in self._closed

    def intent_of(self, order_id: str | None) -> OrderIntent | None:
        return self._order_intents.get(order_id) if order_id is not None else None

    def cancel(self, intent: OrderIntent) -> None:
        for index, queued in enumerate(self._outbox):
            if queued is intent:  # not sent yet: withdraw it
                del self._outbox[index]
                self.release(intent)
                return
        order_id = self._intent_orders.get(id(intent))
        if (
            order_id is not None
            and order_id not in self._closed
            and order_id not in self._cancel_asked
        ):
            self._cancel_asked.add(order_id)
            self._cancels.append(order_id)

    # -- runner side ----------------------------------------------------------
    def note_order(self, order_id: str, intent: OrderIntent) -> None:
        """Record that the runner sent ``intent`` as ``order_id`` (enables ``cancel``)."""
        self._order_intents[order_id] = intent
        self._intent_orders[id(intent)] = order_id

    def note_closed(self, order_id: str) -> None:
        """Record that ``order_id`` finished (filled, rejected, cancelled or expired)."""
        self._closed.add(order_id)

    def drain_cancels(self) -> list[str]:
        """Return and clear the order ids the strategy asked to cancel, in request order."""
        ids, self._cancels = self._cancels, []
        return ids

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
        """Book a fill: position, cash (notional plus costs) and pending quantity.

        The notional rounds to minor units once (ADR 0011) and costs are ``Money``.
        All-or-nothing, like the Rust ledger: a fill whose notional cannot be
        represented or whose costs are in another currency raises ``ValueError``
        and leaves the ledger untouched.
        """
        notional = Money.mul_qty(fill.quantity, fill.price, self.currency)
        if fill.side is OrderSide.BUY:
            cash = self._cash - (notional + fill.costs)
            signed = fill.quantity
        else:
            cash = self._cash + (notional - fill.costs)
            signed = -fill.quantity
        self._cash = cash
        self._positions[fill.instrument_id] = self.position(fill.instrument_id) + signed
        self._reduce_pending(fill.instrument_id, fill.side, fill.quantity)

    def release(self, intent: OrderIntent) -> None:
        """An intent was rejected or its order cancelled unfilled: it no longer counts as busy.

        An invalid intent never counted (see ``submit``), so releasing it is a no-op.
        """
        if not _is_valid(intent):
            return
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
