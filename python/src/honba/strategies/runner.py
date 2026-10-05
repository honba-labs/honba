"""``StrategyRunner``: drives a strategy and an execution port (mirrors ``honba_strategy::StrategyRunner``).

Per event ``(event, ts_init)`` (ADR 008, decision 5):

1. the context clock is set to ``ts_init``;
2. a ``Bar``, ``QuoteTick`` or ``TradeTick`` is dispatched to its hook (other events reach no hook);
3. every intent submitted since the last drain (including from ``on_start`` and from the previous
   event's ``on_fill``) is validated and sent to the execution port as order ``"{name}-{n}"``;
4. fills drained from the port are booked in the context, then passed to ``on_fill``.

Intents submitted in ``on_stop`` are never executed.

Logged event types mirror ``honba-messages::Event`` wire types exactly so that
``honba.log.EventFilter`` can filter them by wire name:

* ``order``          – intent submitted to the execution port
* ``order_rejected`` – intent failed validation (WARNING level, always admitted)
* ``order_filled``   – fill received from the execution port
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

import honba.log as _honba_log  # noqa: F401 — triggers auto-init from HONBA_LOG_EVENTS
from honba.entities.bar import Bar
from honba.entities.order import OrderIntent, validate_intent
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext


class ExecutionPort(Protocol):
    """Where orders go: a simulator in backtest, a broker adapter in live."""

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def drain_fills(self) -> list[Trade]: ...


@dataclass(frozen=True, slots=True)
class SubmittedIntent:
    """An intent the runner sent as an order, with the ``ts_init`` it was sent at."""

    ts_init: int
    intent: OrderIntent
    order_id: str


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


def _overrides(strategy: Strategy, method: str) -> bool:
    return getattr(type(strategy), method) is not getattr(Strategy, method)


class StrategyRunner:
    """Binds ``ctx`` (a fresh ``LedgerContext`` by default) to ``strategy`` and runs it."""

    def __init__(
        self,
        strategy: Strategy,
        execution: ExecutionPort,
        ctx: LedgerContext | None = None,
    ) -> None:
        self.strategy = strategy
        self.execution = execution
        self.ctx = ctx if ctx is not None else LedgerContext()
        strategy.bind(self.ctx)
        # Logger name: honba.runner.<strategy-name>
        # Filter with: EventFilter(["order_filled", "order_rejected", ...])
        self.logger = logging.getLogger(f"honba.runner.{strategy.name}")
        self._seq = 0
        self.intents: list[SubmittedIntent] = []
        self.fills: list[Trade] = []
        self.rejections: list[IntentRejection] = []

    def start(self) -> None:
        self.strategy.on_start()

    def on_event(self, event: Any, ts_init: int) -> None:
        self.ctx.set_now(ts_init)
        if isinstance(event, Bar):
            self.strategy.on_bar(event)
        elif isinstance(event, QuoteTick):
            self.strategy.on_quote(event)
        elif isinstance(event, TradeTick):
            self.strategy.on_trade(event)
        self._submit_intents(ts_init)
        self._book_fills()

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
        return RunResult(self.intents, self.fills, self.rejections, self.ctx)

    def _drain(self) -> list[OrderIntent]:
        if _overrides(self.strategy, "drain_intents"):
            return self.strategy.drain_intents()  # deprecated override, honoured until 0.3
        return self.ctx.drain_intents()

    def _submit_intents(self, ts_init: int) -> None:
        for intent in self._drain():
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
                if _overrides(self.strategy, "handle_rejected"):
                    self.strategy.handle_rejected(intent)
                else:
                    self.ctx.release(intent)
                continue

            order_id = f"{self.strategy.name}-{self._seq}"
            self._seq += 1
            self.execution.submit(order_id, intent, ts_init)
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

    def _book_fills(self) -> None:
        for fill in self.execution.drain_fills():
            if _overrides(self.strategy, "handle_fill"):
                self.strategy.handle_fill(fill)  # deprecated override, honoured until 0.3
            else:
                self.ctx.apply_fill(fill)
                self.strategy.on_fill(fill)
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
