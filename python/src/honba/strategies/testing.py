"""Deterministic harnesses for strategy tests.

``replay`` is the minimal bar replay: market intents fill at the close of their
respective instrument (or ``fill_delay`` bars later at the open), with no costs.
Sells fill before buys within each batch to prevent insufficient funds on rebalances.
``BarCloseFills`` is the simulated execution port of the conformance suite
(mirrors ``honba_sim::BarFillEngine``). Use ``honba.backtest`` for realistic simulation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from honba.domain.money import Currency, Money
from honba.entities import wire
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext

MAX_FLAT_COST = 1e9
"""Largest accepted flat cost per fill (mirrors ``honba_sim::MAX_FLAT_COST``)."""

MAX_COST_BPS = 10_000.0
"""Largest accepted proportional cost in bps of the fill notional (100%)."""


class FillCostsError(ValueError):
    """Invalid ``BarCloseFills`` cost parameter (not finite, negative or above its cap)."""


def _checked(name: str, value: float, cap: float) -> float:
    if not (math.isfinite(value) and 0.0 <= value <= cap):
        raise FillCostsError(f"{name} must be finite and within 0..={cap}, got {value}")
    return float(value)


@dataclass
class ReplayResult:
    intents: list[OrderIntent] = field(default_factory=list)
    fills: list[Trade] = field(default_factory=list)


def replay(strategy: Strategy, bars: Iterable[Bar], fill_delay: int = 0) -> ReplayResult:
    """Replays ``bars``. ``fill_delay=0`` fills at the instrument's last close;
    ``fill_delay=n`` fills at the open of the bar ``n`` bars later, like a live runner.
    Within each fill batch, sells execute before buys so liquidation funds purchases."""
    result = ReplayResult()
    queue: list[tuple[int, OrderIntent]] = []  # (due bar index, intent)
    last_close: dict[InstrumentId, float] = {}
    last_open: dict[InstrumentId, float] = {}
    strategy.on_start()
    for i, bar in enumerate(bars):
        last_close[bar.instrument_id] = bar.close
        last_open[bar.instrument_id] = bar.open
        if isinstance(strategy.ctx, LedgerContext):
            strategy.ctx.set_now(bar.ts)
        due = [q for q in queue if q[0] <= i]
        queue = [q for q in queue if q[0] > i]
        due.sort(key=lambda item: (0 if item[1].side is OrderSide.SELL else 1))
        for _, intent in due:
            px = last_open.get(intent.instrument_id, bar.open)
            _fill(strategy, result, intent, px, bar.ts)
        strategy.on_bar(bar)
        intents = strategy.drain_intents()
        for intent in intents:
            result.intents.append(intent)
        if fill_delay == 0:
            intents_to_fill = sorted(
                intents, key=lambda it: (0 if it.side is OrderSide.SELL else 1)
            )
            for intent in intents_to_fill:
                px = last_close.get(intent.instrument_id, bar.close)
                _fill(strategy, result, intent, px, bar.ts)
        else:
            for intent in intents:
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

    Fill time is ``max(order ts, previous fill ts + 1)``. This is the
    ``"bar_close"`` fill model of the conformance fixture and mirrors Rust's
    ``BarFillEngine``, except that an order before any bar raises instead of
    filling at 0.0 (a known gap of the Rust engine, ADR 006).

    Costs (ADR 008) default to none. With ``flat_cost`` (``0..=MAX_FLAT_COST``)
    and ``cost_bps`` (``0..=MAX_COST_BPS``) every fill carries ``Trade.costs`` =
    the flat leg plus ``(quantity * price) * cost_bps / 10_000``, **each leg rounded
    to minor units once before summation** (ADR 0011), as ``Money`` in ``currency``
    and not signed by side. Invalid values raise ``FillCostsError``; a cost that
    cannot be represented raises ``ValueError`` instead of charging zero.
    """

    def __init__(
        self, flat_cost: float = 0.0, cost_bps: float = 0.0, currency: Currency = Currency.INR
    ) -> None:
        self._flat_cost = _checked("flat_cost", flat_cost, MAX_FLAT_COST)
        self._cost_bps = _checked("cost_bps", cost_bps, MAX_COST_BPS)
        self._currency = currency
        self._last_price: float | None = None
        self._next_ts = 0
        self._fills: list[Trade] = []

    def on_event(self, event: Any, ts_init: int) -> None:
        if isinstance(event, Bar):
            self._last_price = event.close

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if self._last_price is None:
            raise RuntimeError(f"order {order_id} submitted before any bar: no price to fill at")
        costs = self._costs(intent.quantity, self._last_price)
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
                costs=costs,
            )
        )

    def _costs(self, quantity: float, price: float) -> Money:
        # Per leg, then sum (ADR 0011): the same float operations as Rust's FillCosts.
        flat = Money.from_major(self._flat_cost, self._currency)
        bps = Money.from_major(quantity * price * self._cost_bps / 10_000.0, self._currency)
        return flat + bps

    def drain_fills(self) -> list[Trade]:
        fills, self._fills = self._fills, []
        return fills


def from_wire_messages(payload: str | bytes) -> list[tuple[Bar | QuoteTick | TradeTick, int]]:
    """Parse a JSON list of wire ``Message``s into ``(domain event, ts_init)`` pairs.

    Parsed strictly (``honba.entities.wire.loads_many``). Only market data (bar,
    quote, trade) is supported. The domain shapes keep the event time as ``ts``
    (integer nanoseconds, from the wire ``{iso, unix_nanos}`` object); the bar
    specification is dropped.
    """
    out: list[tuple[Bar | QuoteTick | TradeTick, int]] = []
    for message in wire.loads_many("Message", payload):
        event = message.event
        domain: Bar | QuoteTick | TradeTick
        if isinstance(event, wire.BarEvent):
            domain = Bar(
                event.bar_type.instrument_id.to_domain(),
                event.ts_event.to_ns(),
                event.open,
                event.high,
                event.low,
                event.close,
                event.volume,
            )
        elif isinstance(event, wire.QuoteEvent):
            domain = QuoteTick(
                event.instrument_id.to_domain(),
                event.ts_event.to_ns(),
                event.bid_price,
                event.ask_price,
                event.bid_size,
                event.ask_size,
            )
        elif isinstance(event, wire.TradeEvent):
            domain = TradeTick(
                event.instrument_id.to_domain(),
                event.ts_event.to_ns(),
                event.price,
                event.size,
                event.aggressor_side,
                event.trade_id,
            )
        else:
            raise ValueError(  # noqa: TRY004 - a valid wire Event of an unsupported kind
                f"unsupported event type {event.type!r}; expected bar, quote or trade"
            )
        out.append((domain, message.ts_init.to_ns()))
    return out
