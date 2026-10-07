"""The ``ExecutionPort`` contract: where the runner sends orders (ADR 008, ADR 0019).

A simulator in backtest, a broker adapter in live. The runner owns the strategy's
context; a port never touches it. Everything a port has to say about an order goes back
through **one ordered queue** the runner drains after every event:

* ``drain_events()``: a list of :data:`ExecutionEvent` (``Submitted``, ``Accepted``,
  ``Rejected``, ``Fill``, ``CancelRequested``, ``Cancelled``, ``Expired``) in the order
  they happened. The runner keeps an ``OrderState`` per order, books each ``Fill`` and
  releases the unfilled remainder a ``Rejected`` / ``Cancelled`` / ``Expired`` carries,
  so the strategy no longer sees the instrument as busy. Queue order is the tiebreak
  between events at the same time (a fill that beats a cancel comes first).
* ``cancel(order_id, now)``: asks the port to cancel a working order, stamped with the
  engine time ``now`` the cancel was processed at; the answer is a ``Cancelled`` event.
  Cancelling an unknown or already finished order is a no-op.

Shims (ADR 0019 decision 4; present in 0.1.x and 0.2.x, removed in 0.3.0):

* *new port, legacy caller*: :class:`BaseExecutionPort` keeps ``drain_fills()`` and
  ``drain_rejections()`` as a buffered split of one ``drain_events()`` call. Neither
  event kind is lost; only the cross-queue order is.
* *legacy port, new runner*: a port with only ``drain_fills`` (and optionally
  ``drain_rejections`` and a one-argument ``cancel(order_id)``) is adapted by
  :func:`adapt_port`: :class:`LegacyPortEvents` returns its fills, then its rejections,
  as events, and ``inspect.signature`` picks the ``cancel`` form (the one-argument form
  emits a ``DeprecationWarning``). :func:`cancel_order` and :func:`drain_port_rejections`
  are deprecated helpers over the same logic.

``OrderRejection`` stays as the legacy type, derived from ``Rejected`` / ``Cancelled`` /
``Expired`` events by :func:`rejection_from_event`.

Pure contract: no I/O, no wall clock.
"""

from __future__ import annotations

import inspect
import warnings
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
    "EXPIRED_REASON",
    "Accepted",
    "AdaptedPort",
    "BaseExecutionPort",
    "CancelRequested",
    "Cancelled",
    "ExecutionEvent",
    "ExecutionPort",
    "ExecutionPortLike",
    "Expired",
    "Fill",
    "LegacyExecutionPort",
    "LegacyPortEvents",
    "OrderRejection",
    "Rejected",
    "RejectingExecutionPort",
    "Submitted",
    "adapt_port",
    "cancel_order",
    "drain_port_rejections",
    "event_from_rejection",
    "event_order_id",
    "events_from_native",
    "order_event",
    "rejection_from_event",
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


EXPIRED_REASON = "expired"
"""The legacy ``OrderRejection.reason`` an ``Expired`` event is shown as (Rust ``EXPIRED``)."""

_QTY_EPS = 1e-9  # the ADR 0016 tolerance, as in ``OrderState``


@runtime_checkable
class ExecutionPort(Protocol):
    """The port surface (ADR 0019): accept orders, cancel them, hand back one ordered stream."""

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def cancel(self, order_id: str, now: int) -> None: ...

    def drain_events(self) -> list[ExecutionEvent]: ...


@runtime_checkable
class LegacyExecutionPort(Protocol):
    """The pre-0019 required surface: ``submit`` and ``drain_fills`` (shimmed until 0.3.0)."""

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def drain_fills(self) -> list[Trade]: ...


@runtime_checkable
class RejectingExecutionPort(LegacyExecutionPort, Protocol):
    """A pre-0019 port that also reports rejections and accepts a one-argument cancel."""

    def cancel(self, order_id: str) -> None: ...

    def drain_rejections(self) -> list[OrderRejection]: ...


ExecutionPortLike = ExecutionPort | LegacyExecutionPort
"""Anything the runner accepts: an event port, or a legacy port adapted by :func:`adapt_port`."""


def rejection_from_event(ev: ExecutionEvent) -> OrderRejection | None:
    """The legacy :class:`OrderRejection` of a terminal event, or ``None`` for the others.

    ``Rejected`` keeps its reason, ``Cancelled`` is ``reason == "cancelled"`` and ``Expired``
    is ``reason == "expired"``; the intent carries the unfilled remainder that is released.
    """
    if isinstance(ev, Rejected):
        return OrderRejection(
            ev.order_id, ev.intent, ev.reason, ev.ts, cancelled=ev.reason == CANCELLED_REASON
        )
    if isinstance(ev, Cancelled):
        return OrderRejection(ev.order_id, ev.intent, CANCELLED_REASON, ev.ts, cancelled=True)
    if isinstance(ev, Expired):
        return OrderRejection(ev.order_id, ev.intent, EXPIRED_REASON, ev.ts)
    return None


def event_from_rejection(r: OrderRejection) -> Rejected | Cancelled:
    """The terminal event a legacy rejection stands for (``Cancelled`` iff ``r.cancelled``)."""
    if r.cancelled:
        return Cancelled(r.order_id, r.intent, r.ts)
    return Rejected(r.order_id, r.intent, r.reason, r.ts)


def _overrides(obj: object, name: str, base: type) -> bool:
    return getattr(type(obj), name, None) is not getattr(base, name, None)


class LegacyPortEvents:
    """Wraps a port that only has ``drain_fills`` / ``drain_rejections`` (ADR 0019 shim).

    ``drain_events()`` returns ``[Fill ..]`` for ``drain_fills()`` followed by
    ``[Rejected | Cancelled ..]`` for ``drain_rejections()``: the documented *legacy* order
    (the two queues never had a relative order). A fill's ``cum_qty`` and ``complete`` are
    derived here from the quantity ``submit`` saw (``complete`` stays false for an order
    submitted around the wrapper). The wrapper forwards ``submit`` and ``cancel``.
    """

    def __init__(self, port: object) -> None:
        self.port = port
        self._ordered: dict[str, float] = {}
        self._cum: dict[str, float] = {}

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self.port.submit(order_id, intent, ts)  # type: ignore[attr-defined]
        self._ordered[order_id] = intent.quantity
        self._cum.pop(order_id, None)

    def cancel(self, order_id: str, now: int) -> bool:
        return cancel_order(self.port, order_id, now)

    def drain_events(self) -> list[ExecutionEvent]:
        out: list[ExecutionEvent] = []
        drain_fills = getattr(self.port, "drain_fills", None)
        for trade in drain_fills() if drain_fills is not None else []:
            oid = trade.order_id or ""
            cum = self._cum.get(oid, 0.0) + trade.quantity
            self._cum[oid] = cum
            ordered = self._ordered.get(oid)
            complete = ordered is not None and cum + _QTY_EPS >= ordered
            out.append(Fill(trade, cum, complete))
        out.extend(event_from_rejection(r) for r in drain_port_rejections(self.port))
        return out


def _cancel_binder(port: object, *, warn: bool = True) -> Callable[[str, int], None] | None:
    """``port.cancel`` as ``(order_id, now)``, adapting a one-argument legacy cancel.

    ``inspect.signature`` decides: two positional parameters (or ``*args``) are the current
    ``cancel(order_id, now)``; one is the legacy ``cancel(order_id)`` and warns once
    (``DeprecationWarning``, removal in 0.3.0). ``None`` if the port cannot cancel at all.
    """
    cancel = getattr(port, "cancel", None)
    if cancel is None:
        return None
    try:
        params = list(inspect.signature(cancel).parameters.values())
    except (TypeError, ValueError):  # builtins and the like: assume the current form
        return cancel
    positional = [
        p
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2 or any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
        return cancel
    if warn:
        warnings.warn(
            f"{type(port).__name__}.cancel(order_id) is deprecated: implement "
            "cancel(order_id, now) (ADR 0019); the one-argument form is removed in 0.3.0",
            DeprecationWarning,
            stacklevel=3,
        )
    return lambda order_id, now: cancel(order_id)


class AdaptedPort:
    """What the runner talks to: ``submit``, ``cancel(order_id, now)``, ``drain_events()``.

    Built by :func:`adapt_port` once, at bind time. An event port is passed through; a
    legacy port is wrapped in :class:`LegacyPortEvents`. ``cancel`` returns ``False`` if
    the port has no cancel path.
    """

    def __init__(self, port: object) -> None:
        self.port = port
        self._events: LegacyPortEvents | None = None
        if not _has_events(port):
            self._events = LegacyPortEvents(port)
        self._cancel = _cancel_binder(port)

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if self._events is not None:
            self._events.submit(order_id, intent, ts)
        else:
            self.port.submit(order_id, intent, ts)  # type: ignore[attr-defined]

    def cancel(self, order_id: str, now: int) -> bool:
        if self._cancel is None:
            return False
        self._cancel(order_id, now)
        return True

    def drain_events(self) -> list[ExecutionEvent]:
        if self._events is not None:
            return self._events.drain_events()
        return list(self.port.drain_events())  # type: ignore[attr-defined]


def _has_events(port: object) -> bool:
    drain = getattr(port, "drain_events", None)
    if drain is None:
        return False
    if isinstance(port, BaseExecutionPort):
        return _overrides(port, "drain_events", BaseExecutionPort)
    return True


def adapt_port(port: object) -> AdaptedPort:
    """Adapt ``port`` (event port or legacy port) for the runner; see :class:`AdaptedPort`."""
    return AdaptedPort(port)


class BaseExecutionPort(ABC):
    """Convenience base: implement ``submit`` and ``drain_events``; the rest defaults.

    ``cancel(order_id, now)`` defaults to inert (nothing is working). The legacy drains are
    the buffered shim over ``drain_events`` (ADR 0019): each call runs ``drain_events()``
    once and splits the result into a fill buffer and a rejection buffer, returns and
    clears only its own, and loses nothing. A port written before the event stream may
    instead override ``drain_fills`` / ``drain_rejections`` (and a one-argument ``cancel``);
    its ``drain_events`` then reads them (fills, then rejections). Removed in 0.3.0.
    """

    @abstractmethod
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def cancel(self, order_id: str, now: int) -> None:
        """Cancel a working order. Default: nothing is working, so nothing to cancel."""

    def drain_events(self) -> list[ExecutionEvent]:
        """Return and clear the ordered events. Default: the legacy drains, or nothing."""
        if _overrides(self, "drain_fills", BaseExecutionPort) or _overrides(
            self, "drain_rejections", BaseExecutionPort
        ):
            legacy: LegacyPortEvents | None = getattr(self, "_legacy_events", None)
            if legacy is None:
                legacy = self._legacy_events = LegacyPortEvents(self)  # type: ignore[attr-defined]
            return legacy.drain_events()
        return []

    # -- buffered shim: the legacy pair over drain_events (removed in 0.3.0) -----------------
    def _shim_buffers(self) -> tuple[list[Trade], list[OrderRejection]]:
        buffers: tuple[list[Trade], list[OrderRejection]] | None = getattr(self, "_shim_buf", None)
        if buffers is None:
            buffers = ([], [])
            self._shim_buf = buffers  # type: ignore[attr-defined]
        return buffers

    def _pump_shim(self) -> tuple[list[Trade], list[OrderRejection]]:
        fills, rejections = self._shim_buffers()
        if _overrides(self, "drain_events", BaseExecutionPort):
            for ev in self.drain_events():
                if isinstance(ev, Fill):
                    fills.append(ev.trade)
                elif (r := rejection_from_event(ev)) is not None:
                    rejections.append(r)
        return fills, rejections

    def drain_fills(self) -> list[Trade]:
        """Legacy view: the fills of one ``drain_events`` call (rejections stay buffered)."""
        fills, _ = self._pump_shim()
        out = list(fills)
        fills.clear()
        return out

    def drain_rejections(self) -> list[OrderRejection]:
        """Legacy view: the rejections of one ``drain_events`` call (fills stay buffered)."""
        _, rejections = self._pump_shim()
        out = list(rejections)
        rejections.clear()
        return out


def drain_port_rejections(port: object) -> list[OrderRejection]:
    """``port.drain_rejections()``, or ``[]`` for a port without the reject path (deprecated)."""
    drain = getattr(port, "drain_rejections", None)
    return list(drain()) if drain is not None else []


def cancel_order(port: object, order_id: str, now: int = 0) -> bool:
    """Ask ``port`` to cancel ``order_id``; ``False`` if the port cannot cancel at all.

    A legacy one-argument ``cancel(order_id)`` is adapted (``DeprecationWarning``).
    """
    cancel = _cancel_binder(port)
    if cancel is None:
        return False
    cancel(order_id, now)
    return True
