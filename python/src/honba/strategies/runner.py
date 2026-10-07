"""``StrategyRunner``: drives a strategy and an execution port (mirrors ``honba_strategy::StrategyRunner``).

Per event ``(event, ts_init)`` (ADR 008, decision 5):

1. the context clock is set to ``ts_init``;
2. a ``Bar``, ``QuoteTick`` or ``TradeTick`` is dispatched to its hook (other events reach no hook);
3. every intent submitted since the last drain (including from ``on_start`` and from the previous
   event's ``on_fill``) is validated and sent to the execution port as order ``"{name}-{n}"``;
4. the port's one ordered event stream (``drain_events()``, ADR 0019 decision 4) is drained
   once and walked in order, keeping an ``OrderState`` per order: a ``Fill`` is booked in the
   context, then passed to ``on_fill``; a ``Rejected``, ``Cancelled`` or ``Expired`` event
   releases the unfilled remainder it carries (for the instrument it names) and is recorded as
   an ``OrderRejection``. A repeated or illegal terminal event for an order releases nothing,
   so ``filled + released == ordered`` per order. Queue order is the tiebreak (a fill that
   beats a cancel comes first), as in the Rust runner. Legacy ports (``drain_fills`` /
   ``drain_rejections``, one-argument ``cancel``) are adapted by ``adapt_port`` until 0.3.0.

Intents submitted in ``on_stop`` are never executed.

Warm-up gate (``StrategyManifest.warmup_bars``): a *driving bar* is a ``Bar`` event whose
``ts_init`` differs from the previous ``Bar`` event's (bars of several instruments at one
time are one driving bar). While at most ``warmup_bars`` driving bars have been seen (and
before the first one), the strategy still receives every event, but each valid intent is
released in the context and recorded as a :class:`SuppressedIntent` instead of becoming an
order; it consumes no order id. Invalid intents are rejected as usual. Vectors shared with
Rust: ``schema/conformance/warmup_gate.json``.

Logged event types mirror ``honba-messages::Event`` wire types exactly so that
``honba.log.EventFilter`` can filter them by wire name:

* ``order``          – intent submitted to the execution port
* ``order_rejected`` – intent failed validation, or the port rejected an order (WARNING level)
* ``order_filled``   – fill received from the execution port
* ``order_cancelled`` – the port cancelled an order (WARNING level)
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import honba.log as _honba_log  # noqa: F401 — triggers auto-init from HONBA_LOG_EVENTS
from honba.domain.order_state import IllegalTransition, OrderEvent, OrderState
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, validate_intent
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.execution import (
    ExecutionEvent,
    ExecutionPort,
    ExecutionPortLike,
    Fill,
    OrderRejection,
    adapt_port,
    event_order_id,
    order_event,
    rejection_from_event,
)

__all__ = [
    "ExecutionPort",
    "ExecutionPortLike",
    "IntentRejection",
    "OrderRejection",
    "RunResult",
    "StrategyRunner",
    "SubmittedIntent",
    "SuppressedIntent",
]


@dataclass(frozen=True, slots=True)
class SubmittedIntent:
    """An intent the runner sent as an order, with the ``ts_init`` it was sent at."""

    ts_init: int
    intent: OrderIntent
    order_id: str


@dataclass(frozen=True, slots=True)
class SuppressedIntent:
    """A valid intent the warm-up gate released instead of sending, with its ``ts_init``."""

    ts_init: int
    intent: OrderIntent


@dataclass(frozen=True, slots=True)
class IntentRejection:
    """An intent the runner refused because it breaks an ``OrderIntent`` invariant."""

    ts_init: int
    intent: OrderIntent
    error: str


@dataclass
class RunResult:
    intents: list[SubmittedIntent] = field(default_factory=list)
    fills: list[Trade] = field(default_factory=list)
    rejections: list[IntentRejection] = field(default_factory=list)
    ctx: LedgerContext = field(default_factory=LedgerContext)
    # Orders (or parts of them) the port rejected or cancelled, released in ``ctx``.
    order_rejections: list[OrderRejection] = field(default_factory=list)
    # Intents the warm-up gate released instead of sending.
    suppressed: list[SuppressedIntent] = field(default_factory=list)


_QTY_EPS = 1e-9  # ADR 0016 tolerance, as in ``OrderState``


def _overrides(strategy: Strategy, method: str) -> bool:
    return getattr(type(strategy), method) is not getattr(Strategy, method)


class StrategyRunner:
    """Binds ``ctx`` (a fresh ``LedgerContext`` by default) to ``strategy`` and runs it.

    ``warmup_bars`` defaults to the strategy's ``warmup_bars`` class attribute (0).
    """

    def __init__(
        self,
        strategy: Strategy,
        execution: ExecutionPortLike,
        ctx: LedgerContext | None = None,
        *,
        warmup_bars: int | None = None,
    ) -> None:
        if warmup_bars is None:
            warmup_bars = getattr(strategy, "warmup_bars", 0)
        if isinstance(warmup_bars, bool) or not isinstance(warmup_bars, int):
            raise TypeError(f"warmup_bars must be an int, got {warmup_bars!r}")
        if warmup_bars < 0:
            raise ValueError(f"warmup_bars must be >= 0, got {warmup_bars}")
        self.warmup_bars = warmup_bars
        self._bars_seen = 0
        self._last_bar_ts: int | None = None
        self.strategy = strategy
        self.execution = execution
        self._port = adapt_port(execution)  # event stream + cancel(order_id, now), bound once
        self._now = 0
        self._states: dict[str, OrderState] = {}
        self._released: dict[InstrumentId, float] = {}
        self.ctx = ctx if ctx is not None else LedgerContext()
        strategy.bind(self.ctx)
        # Logger name: honba.runner.<strategy-name>
        # Filter with: EventFilter(["order_filled", "order_rejected", ...])
        self.logger = logging.getLogger(f"honba.runner.{strategy.name}")
        self._seq = 0
        self.intents: list[SubmittedIntent] = []
        self.fills: list[Trade] = []
        self.rejections: list[IntentRejection] = []
        self.order_rejections: list[OrderRejection] = []
        self.suppressed: list[SuppressedIntent] = []

    def start(self) -> None:
        self.strategy.on_start()

    @property
    def warming_up(self) -> bool:
        """True while orders are suppressed: at most ``warmup_bars`` driving bars seen."""
        return self.warmup_bars > 0 and self._bars_seen <= self.warmup_bars

    def on_event(self, event: Any, ts_init: int) -> None:
        self.ctx.set_now(ts_init)
        self._now = ts_init
        if isinstance(event, Bar) and ts_init != self._last_bar_ts:
            self._bars_seen += 1
            self._last_bar_ts = ts_init
        if isinstance(event, Bar):
            self.strategy.on_bar(event)
        elif isinstance(event, QuoteTick):
            self.strategy.on_quote(event)
        elif isinstance(event, TradeTick):
            self.strategy.on_trade(event)
        self._submit_intents(ts_init)
        self._book_events()

    def order_state(self, order_id: str) -> OrderState | None:
        """The FSM state the runner tracked for ``order_id``, if it submitted it."""
        return self._states.get(order_id)

    def released_quantity(self, instrument_id: InstrumentId) -> float:
        """Total quantity released for ``instrument_id``: its rejected/cancelled/expired remainders."""
        return self._released.get(instrument_id, 0.0)

    def cancel(self, order_id: str) -> bool:
        """Ask the port to cancel ``order_id`` and book what it reports at once.

        The cancel is stamped with the time of the latest event (``now``). Returns ``False``
        if the port has no cancel path (nothing is released then). Cancelling an unknown or
        finished order does nothing.
        """
        if not self._port.cancel(order_id, self._now):
            return False
        state = self._states.get(order_id)
        if state is not None:
            try:
                state.apply(OrderEvent.cancel_requested())
            except IllegalTransition:
                pass  # finished order: the FSM keeps its state
        self._book_events()
        return True

    def stop(self) -> None:
        self.strategy.on_stop()
        self.ctx.drain_intents()  # never executed: the run is over

    def run(self, events: Iterable[tuple[Any, int]]) -> RunResult:
        """``start``, then each ``(event, ts_init)``, then ``stop``.

        An execution port with an ``on_event(event, ts_init)`` method (a simulator)
        sees each event before the strategy, like an engine handler registered first.
        """
        observe = getattr(self.execution, "on_event", None)
        self.start()
        for event, ts_init in events:
            if observe is not None:
                observe(event, ts_init)
            self.on_event(event, ts_init)
        self.stop()
        return RunResult(
            self.intents,
            self.fills,
            self.rejections,
            self.ctx,
            order_rejections=self.order_rejections,
            suppressed=self.suppressed,
        )

    def _drain(self) -> list[OrderIntent]:
        if _overrides(self.strategy, "drain_intents"):
            return self.strategy.drain_intents()  # deprecated override, honoured until 0.3
        return self.ctx.drain_intents()

    def _submit_intents(self, ts_init: int) -> None:
        drained = self._drain()
        for index, intent in enumerate(drained):
            try:
                validate_intent(
                    intent.side,
                    intent.quantity,
                    intent.order_type,
                    intent.price,
                    intent.trigger_price,
                )
            except ValueError as error:
                self.rejections.append(IntentRejection(ts_init, intent, str(error)))
                # Wire type: order_rejected — WARNING always passes EventFilter
                self.logger.warning(
                    "order_rejected: order_id=pending symbol=%s side=%s reason=%s",
                    intent.instrument_id.symbol,
                    intent.side.name,
                    error,
                    extra={
                        "event_type": "order_rejected",
                        "symbol": intent.instrument_id.symbol,
                        "reason": str(error),
                    },
                )
                self._release(intent)
                continue

            if self.warming_up:
                self.ctx.release(intent)
                self.suppressed.append(SuppressedIntent(ts_init, intent))
                self.logger.debug(
                    "warmup: suppressed symbol=%s side=%s qty=%s",
                    intent.instrument_id.symbol,
                    intent.side.name,
                    intent.quantity,
                )
                continue

            order_id = f"{self.strategy.name}-{self._seq}"
            self._seq += 1
            try:
                self._port.submit(order_id, intent, ts_init)
            except BaseException:
                # Terminal for the run, but the intents that were drained and never
                # sent (this one and the rest) must not leave their instruments busy.
                for unsent in drained[index:]:
                    self._release(unsent)
                raise
            state = OrderState()
            state.apply(OrderEvent.submitted(intent.quantity))
            self._states[order_id] = state
            self.intents.append(SubmittedIntent(ts_init, intent, order_id))
            # Wire type: order (DEBUG — not emitted unless level <= DEBUG)
            self.logger.debug(
                "order: order_id=%s symbol=%s side=%s qty=%s",
                order_id,
                intent.instrument_id.symbol,
                intent.side.name,
                intent.quantity,
                extra={
                    "event_type": "order",
                    "order_id": order_id,
                    "symbol": intent.instrument_id.symbol,
                    "side": intent.side.name,
                    "qty": intent.quantity,
                },
            )

    def _book_events(self) -> None:
        # A failing hook does not lose the other drained events: each is still booked
        # and recorded, and the first error is raised afterwards.
        first_error: Exception | None = None
        for ev in self._port.drain_events():
            try:
                self._book_event(ev)
            except Exception as error:  # noqa: BLE001 - the first one is re-raised below
                first_error = first_error or error
        if first_error is not None:
            raise first_error

    def _book_event(self, ev: ExecutionEvent) -> None:
        if isinstance(ev, Fill):
            state = self._states.get(ev.trade.order_id or "")
            if state is not None:
                complete = state.filled_qty + ev.trade.quantity + _QTY_EPS >= state.quantity
                try:
                    state.apply(OrderEvent.fill(ev.trade.quantity, complete))
                except IllegalTransition:
                    pass  # an illegal fill (overfill) leaves the FSM unchanged but is booked
            self._book_fill(ev.trade)
            return
        rejection = rejection_from_event(ev)
        if rejection is None:  # acknowledgements move the FSM only
            state = self._states.get(event_order_id(ev))
            if state is not None:
                try:
                    state.apply(order_event(ev))
                except IllegalTransition:
                    pass
            return
        state = self._states.get(rejection.order_id)
        if state is not None:
            try:
                transitioned = state.apply(order_event(ev))
            except IllegalTransition:
                return  # illegal terminal: releases nothing
            if not transitioned:
                return  # duplicate terminal: releases nothing
        self._book_rejection(rejection)

    def _book_fill(self, fill: Trade) -> None:
        if _overrides(self.strategy, "handle_fill"):
            # deprecated override, honoured until 0.3
            try:
                self.strategy.handle_fill(fill)
            finally:
                self._record_fill(fill)
            return
        self.ctx.apply_fill(fill)  # a fill that cannot be booked is not recorded
        try:
            self.strategy.on_fill(fill)
        finally:
            self._record_fill(fill)

    def _record_fill(self, fill: Trade) -> None:
        self.fills.append(fill)
        # Wire type: order_filled (INFO)
        # ts_event is the simulated bar date (Unix ns), formatted as YYYY-MM-DD for readability
        ts_ns = fill.ts or 0
        if ts_ns > 0:
            from datetime import datetime, timezone

            bar_date = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            bar_date = "?"
        self.logger.info(
            "order_filled: ts_event=%s order_id=%s symbol=%s side=%s last_qty=%s last_px=%.4f cost=%.4f",
            bar_date,
            fill.order_id or "?",
            fill.instrument_id.symbol,
            fill.side.name,
            fill.quantity,
            fill.price,
            fill.costs.to_major(),
            extra={
                "event_type": "order_filled",
                "ts_event": bar_date,
                "order_id": fill.order_id,
                "symbol": fill.instrument_id.symbol,
                "side": fill.side.name,
                "last_qty": fill.quantity,
                "last_px": fill.price,
                "cost": fill.costs.to_major(),
            },
        )

    def _release(self, intent: OrderIntent) -> None:
        if _overrides(self.strategy, "handle_rejected"):
            self.strategy.handle_rejected(intent)  # deprecated override, honoured until 0.3
        else:
            self.ctx.release(intent)

    def _book_rejection(self, rejection: OrderRejection) -> None:
        iid, qty = rejection.intent.instrument_id, rejection.intent.quantity
        if math.isfinite(qty) and qty > 0:
            self._released[iid] = self._released.get(iid, 0.0) + qty
        self._release(rejection.intent)
        self.order_rejections.append(rejection)
        event_type = "order_cancelled" if rejection.cancelled else "order_rejected"
        self.logger.warning(
            "%s: order_id=%s symbol=%s side=%s qty=%s reason=%s",
            event_type,
            rejection.order_id,
            rejection.intent.instrument_id.symbol,
            rejection.intent.side.name,
            rejection.intent.quantity,
            rejection.reason,
            extra={
                "event_type": event_type,
                "order_id": rejection.order_id,
                "symbol": rejection.intent.instrument_id.symbol,
                "reason": rejection.reason,
            },
        )
