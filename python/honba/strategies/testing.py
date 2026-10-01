"""Deterministic harnesses for strategy tests.

``replay`` is the minimal bar replay: market intents fill at the close of the bar
that produced them (or ``fill_delay`` bars later at the open), with no costs.
``BarCloseFills`` is the simulated execution port of the conformance suite
(mirrors ``honba_sim::BarFillEngine``). Use ``honba.backtest`` for realistic simulation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from honba.entities import wire
from honba.entities.bar import Bar
from honba.entities.order import OrderIntent
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext


@dataclass
class ReplayResult:
    intents: list[OrderIntent] = field(default_factory=list)
    fills: list[Trade] = field(default_factory=list)


def replay(strategy: Strategy, bars: Iterable[Bar], fill_delay: int = 0) -> ReplayResult:
    """Replays ``bars``. ``fill_delay=0`` fills at the emitting bar's close;
    ``fill_delay=n`` fills at the open of the bar ``n`` bars later, like a live runner."""
    result = ReplayResult()
    queue: list[tuple[int, OrderIntent]] = []  # (due bar index, intent)
    strategy.on_start()
    for i, bar in enumerate(bars):
        if isinstance(strategy.ctx, LedgerContext):
            strategy.ctx.set_now(bar.ts)
        due = [q for q in queue if q[0] <= i]
        queue = [q for q in queue if q[0] > i]
        for _, intent in due:
            _fill(strategy, result, intent, bar.open, bar.ts)
        strategy.on_bar(bar)
        for intent in strategy.drain_intents():
            result.intents.append(intent)
            if fill_delay == 0:
                _fill(strategy, result, intent, bar.close, bar.ts)
            else:
                queue.append((i + fill_delay, intent))
    strategy.on_stop()
    return result


def _fill(
    strategy: Strategy, result: ReplayResult, intent: OrderIntent, price: float, ts: int
) -> None:
    fill = Trade(intent.instrument_id, intent.side, intent.quantity, price, ts)
    result.fills.append(fill)
    strategy.handle_fill(fill)


class BarCloseFills:
    """Simulated execution: every order fills in full at the latest bar close.

    Fill time is ``max(order ts, previous fill ts + 1)``; no costs. This is the
    ``"bar_close"`` fill model of the conformance fixture and mirrors Rust's
    ``BarFillEngine``, except that an order before any bar raises instead of
    filling at 0.0 (a known gap of the Rust engine, ADR 006).
    """

    def __init__(self) -> None:
        self._last_price: float | None = None
        self._next_ts = 0
        self._fills: list[Trade] = []

    def on_event(self, event: Any, ts_init: int) -> None:
        if isinstance(event, Bar):
            self._last_price = event.close

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if self._last_price is None:
            raise RuntimeError(f"order {order_id} submitted before any bar: no price to fill at")
        fill_ts = max(self._next_ts, ts)
        self._next_ts = fill_ts + 1
        self._fills.append(
            Trade(
                intent.instrument_id,
                intent.side,
                intent.quantity,
                self._last_price,
                fill_ts,
                order_id,
            )
        )

    def drain_fills(self) -> list[Trade]:
        fills, self._fills = self._fills, []
        return fills


def from_wire_messages(payload: str | bytes) -> list[tuple[Bar | QuoteTick | TradeTick, int]]:
    """Parse a JSON list of wire ``Message``s into ``(domain event, ts_init)`` pairs.

    Parsed strictly (``honba.entities.wire.loads_many``). Only market data (bar,
    quote, trade) is supported. The domain shapes keep the event time as ``ts``;
    the bar specification is dropped.
    """
    out: list[tuple[Bar | QuoteTick | TradeTick, int]] = []
    for message in wire.loads_many("Message", payload):
        event = message.event
        domain: Bar | QuoteTick | TradeTick
        if isinstance(event, wire.BarEvent):
            domain = Bar(
                event.bar_type.instrument_id.to_domain(),
                event.ts_event,
                event.open,
                event.high,
                event.low,
                event.close,
                event.volume,
            )
        elif isinstance(event, wire.QuoteEvent):
            domain = QuoteTick(
                event.instrument_id.to_domain(),
                event.ts_event,
                event.bid_price,
                event.ask_price,
                event.bid_size,
                event.ask_size,
            )
        elif isinstance(event, wire.TradeEvent):
            domain = TradeTick(
                event.instrument_id.to_domain(),
                event.ts_event,
                event.price,
                event.size,
                event.aggressor_side,
                event.trade_id,
            )
        else:
            raise ValueError(  # noqa: TRY004 - a valid wire Event of an unsupported kind
                f"unsupported event type {event.type!r}; expected bar, quote or trade"
            )
        out.append((domain, message.ts_init))
    return out
