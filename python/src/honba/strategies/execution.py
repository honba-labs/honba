"""The ``ExecutionPort`` contract: where the runner sends orders (ADR 008).

A simulator in backtest, a broker adapter in live. The runner owns the strategy's
context; a port never touches it. Everything a port has to say about an order goes
back through two queues the runner drains after every event:

* ``drain_fills()`` (required): fills, booked in the context and passed to ``on_fill``;
* ``drain_rejections()`` (optional): an order, or the unfilled part of one, that will
  never fill (rejected by the venue or port, or cancelled). The runner releases it in
  the context so the strategy no longer sees the instrument as busy.

``cancel(order_id)`` (optional) asks the port to cancel a working order; the port
reports the cancelled quantity through ``drain_rejections`` with ``cancelled=True``.
Cancelling an unknown or already finished order is a no-op.

Ports written before the reject/cancel path (only ``submit`` and ``drain_fills``) keep
working: the runner reaches the optional methods through :func:`drain_port_rejections`
and :func:`cancel_order`, which treat a missing method as "nothing to report" /
"cannot cancel". New ports can subclass :class:`BaseExecutionPort` for the defaults.

Pure contract: no I/O, no wall clock.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from honba.domain.money import Currency, Money
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.order_state import OrderEvent
from honba.entities.trade import Trade

__all__ = [
    "CANCELLED_REASON",
    "Accepted",
    "BaseExecutionPort",
    "CancelRequested",
    "Cancelled",
    "ExecutionEvent",
    "ExecutionPort",
    "Expired",
    "Fill",
    "OrderRejection",
    "Rejected",
    "RejectingExecutionPort",
    "Submitted",
    "cancel_order",
    "drain_port_rejections",
    "event_order_id",
    "events_from_native",
    "order_event",
]


CANCELLED_REASON = "cancelled"
"""The ``reason`` of a cancellation (shared with Rust ``OrderRejection::CANCELLED``)."""


@dataclass(frozen=True, slots=True)
class OrderRejection:
    """An order, or the part of one, that will never fill.

    ``intent`` carries the quantity that is released (the unfilled remainder for a
    partial fill). ``cancelled`` distinguishes a cancel (wire ``order_cancelled``)
    from a rejection by the venue or port (wire ``order_rejected``). ``ts`` is the
    port's time for the event in unix ns (0 if it has none). ``cancelled`` and ``reason``
    cannot disagree: ``cancelled`` is true exactly when ``reason == "cancelled"``
    (``CANCELLED_REASON``), as in the Rust ``OrderRejection::is_cancelled``.
    """

    order_id: str
    intent: OrderIntent
    reason: str
    ts: int = 0
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not self.order_id:
            raise ValueError("OrderRejection.order_id must not be empty")
        if self.cancelled != (self.reason == CANCELLED_REASON):
            raise ValueError(
                f"OrderRejection.cancelled={self.cancelled} contradicts reason={self.reason!r}: "
                f"cancelled must be true exactly when the reason is {CANCELLED_REASON!r}"
            )


# --- Port events (ADR 0019 decision 4): the reference for Rust ``ExecutionEvent`` ---------
# ``intent`` carries instrument, side and quantity: the order quantity on ``Submitted``, the
# open quantity on ``Accepted``, the unfilled remainder released on the terminal events.


@dataclass(frozen=True, slots=True)
class Submitted:
    """Submitter-synthesised: the order was handed to the gateway."""

    order_id: str
    intent: OrderIntent
    ts: int


@dataclass(frozen=True, slots=True)
class Accepted:
    """The venue acknowledged the order."""

    order_id: str
    intent: OrderIntent
    ts: int
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Rejected:
    """The order was rejected (pre-gate or venue)."""

    order_id: str
    intent: OrderIntent
    reason: str
    ts: int
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    """A fill; ``complete`` is the producer's claim that it completes the order."""

    trade: Trade
    cum_qty: float
    complete: bool
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class CancelRequested:
    """Submitter-synthesised: a cancel was asked for."""

    order_id: str
    ts: int


@dataclass(frozen=True, slots=True)
class Cancelled:
    """The order was cancelled."""

    order_id: str
    intent: OrderIntent
    ts: int
    venue_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Expired:
    """The order expired by time-in-force."""

    order_id: str
    intent: OrderIntent
    ts: int
    venue_order_id: str | None = None


ExecutionEvent = Submitted | Accepted | Rejected | Fill | CancelRequested | Cancelled | Expired


def event_order_id(ev: ExecutionEvent) -> str:
    """The client order id ``ev`` concerns (a ``Fill`` reads it from its trade)."""
    if isinstance(ev, Fill):
        if not ev.trade.order_id:
            raise ValueError("Fill.trade.order_id must be set to identify the order")
        return ev.trade.order_id
    return ev.order_id


def order_event(ev: ExecutionEvent) -> OrderEvent:
    """Project ``ev`` onto the quantity-only FSM vocabulary (Rust ``order_event``)."""
    if isinstance(ev, Submitted):
        return OrderEvent.submitted(ev.intent.quantity)
    if isinstance(ev, Accepted):
        return OrderEvent.accepted()
    if isinstance(ev, Rejected):
        return OrderEvent.rejected()
    if isinstance(ev, Fill):
        return OrderEvent.fill(ev.trade.quantity, ev.complete)
    if isinstance(ev, CancelRequested):
        return OrderEvent.cancel_requested()
    if isinstance(ev, Cancelled):
        return OrderEvent.cancelled()
    return OrderEvent.expired()


def events_from_native(
    raw: Iterable[Mapping[str, Any]],
    *,
    currency: Currency | str = "INR",
    user_id: Callable[[str], str] = lambda order_id: order_id,
    intent_of: Callable[[str, OrderIntent], OrderIntent] | None = None,
) -> list[ExecutionEvent]:
    """Map ``honba._honba`` ``drain_events()`` dicts to :data:`ExecutionEvent` dataclasses.

    This is the conversion at the binding boundary (ADR 0019 decision 4). Each dict carries a
    ``kind`` (``submitted``, ``accepted``, ``rejected``, ``fill``, ``cancel_requested``,
    ``cancelled``, ``expired``), ``order_id``, ``symbol``, ``exchange``, ``side``, ``quantity``,
    ``ts`` and ``venue_order_id``; a fill adds ``price``, ``costs`` (minor units),
    ``cum_qty`` and ``complete``; a rejection adds ``reason``. ``user_id`` maps the engine's
    order id to the caller's (default identity). ``intent_of(user_order_id, default)`` lets the
    caller substitute the intent it remembers for the order (order type, prices); ``default`` is
    the market intent rebuilt from the event, carrying the event's quantity.
    """
    cur = currency if isinstance(currency, Currency) else Currency(currency)
    out: list[ExecutionEvent] = []
    for d in raw:
        kind = d["kind"]
        oid = user_id(d["order_id"])
        if kind == "cancel_requested":
            out.append(CancelRequested(oid, d["ts"]))
            continue
        side = OrderSide.BUY if d["side"] == "buy" else OrderSide.SELL
        iid = InstrumentId(d["symbol"], d["exchange"])
        venue = d.get("venue_order_id")
        if kind == "fill":
            trade = Trade(
                iid,
                side,
                d["quantity"],
                d["price"],
                d["ts"],
                oid,
                costs=Money.from_minor(d["costs"], cur),
            )
            out.append(Fill(trade, d["cum_qty"], d["complete"], venue))
            continue
        intent = OrderIntent(iid, side, d["quantity"])
        if intent_of is not None:
            intent = intent_of(oid, intent)
        if kind == "submitted":
            out.append(Submitted(oid, intent, d["ts"]))
        elif kind == "accepted":
            out.append(Accepted(oid, intent, d["ts"], venue))
        elif kind == "rejected":
            out.append(Rejected(oid, intent, d["reason"], d["ts"], venue))
        elif kind == "cancelled":
            out.append(Cancelled(oid, intent, d["ts"], venue))
        elif kind == "expired":
            out.append(Expired(oid, intent, d["ts"], venue))
        else:
            raise ValueError(f"unknown native execution event kind {kind!r}")
    return out


@runtime_checkable
class ExecutionPort(Protocol):
    """The required port surface: accept orders, hand back fills."""

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def drain_fills(self) -> list[Trade]: ...


@runtime_checkable
class RejectingExecutionPort(ExecutionPort, Protocol):
    """A port that also reports rejections and accepts cancels."""

    def cancel(self, order_id: str) -> None: ...

    def drain_rejections(self) -> list[OrderRejection]: ...


class BaseExecutionPort(ABC):
    """Convenience base: implement ``submit`` and ``drain_fills``; reject/cancel default inert.

    The defaults suit a port that fills every order it accepts (no rejections) and has
    no working orders to cancel. Override both when the port can hold or refuse orders.
    """

    @abstractmethod
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    @abstractmethod
    def drain_fills(self) -> list[Trade]: ...

    def cancel(self, order_id: str) -> None:
        """Cancel a working order. Default: nothing is working, so nothing to cancel."""

    def drain_rejections(self) -> list[OrderRejection]:
        """Return and clear rejections. Default: this port never rejects."""
        return []


def drain_port_rejections(port: object) -> list[OrderRejection]:
    """``port.drain_rejections()``, or ``[]`` for a port without the reject path."""
    drain = getattr(port, "drain_rejections", None)
    return list(drain()) if drain is not None else []


def cancel_order(port: object, order_id: str) -> bool:
    """Ask ``port`` to cancel ``order_id``; ``False`` if the port cannot cancel at all."""
    cancel = getattr(port, "cancel", None)
    if cancel is None:
        return False
    cancel(order_id)
    return True
