"""Snapshot-diff reducer: the query form as the one ordered event stream (ADR 0019 Appendix A).

An adapter whose only surface is the query form (``order_status`` / ``orders``) still has to
feed the kernel's one-queue execution vocabulary (ADR 0019 decision 4). :func:`reduce_order_report`
turns two consecutive :class:`~honba.adapters.models.OrderReport` snapshots of one order (the
previous one, or ``None`` for a first sighting) into the port events the venue side would have
pushed, in order:

1. ``Accepted`` when the order moved from a pre-ack status to a working one, or when a venue
   order id is first seen;
2. one ``Fill`` for the quantity delta when it grew, priced at the delta-weighted average price
   and ``complete`` iff the new status is ``FILLED``;
3. the terminal ``Rejected`` / ``Cancelled`` / ``Expired`` last.

A decreasing ``filled_quantity`` or a status moving backwards emits nothing and is flagged for
reconciliation (E2-S7): :class:`ReportReduction` carries ``flagged=True``. Polling collapses
several venue fills into one ``Fill`` — a known loss of granularity, not an error.

The reducer never emits ``Submitted`` or ``CancelRequested``: those are the submitter's, so the
caller synthesises them (as the runner does on the port path).
"""

from __future__ import annotations

from dataclasses import dataclass

from honba.adapters.models import OrderReport
from honba.domain.money import Currency, Money
from honba.domain.order import OrderIntent, OrderStatus
from honba.entities.trade import Trade
from honba.strategies.execution import (
    Accepted,
    Cancelled,
    ExecutionEvent,
    Expired,
    Fill,
    Rejected,
)

__all__ = ["ReportReduction", "reduce_order_report"]

_EPS = 1e-9

#: A status is *working* once the venue has acknowledged it. ``FILLED`` is included because a
#: poll can first see a completed order (an instant fill) with no earlier ack to diff against.
_WORKING = frozenset({OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED})
_PRE_ACK = frozenset({OrderStatus.INITIALIZED, OrderStatus.SUBMITTED})
_TERMINAL = frozenset({OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED})

#: Monotone rank for a status moving backwards. Terminal statuses share the top rank, so a move
#: to a terminal from any working state is forward.
_RANK = {
    OrderStatus.INITIALIZED: 0,
    OrderStatus.SUBMITTED: 1,
    OrderStatus.ACCEPTED: 2,
    OrderStatus.PARTIALLY_FILLED: 3,
    OrderStatus.FILLED: 4,
    OrderStatus.CANCELLED: 4,
    OrderStatus.REJECTED: 4,
    OrderStatus.EXPIRED: 4,
}


@dataclass(frozen=True, slots=True)
class ReportReduction:
    """The events one poll produced, plus the reconciliation flag for a backwards move."""

    events: tuple[ExecutionEvent, ...]
    flagged: bool = False


def _open_intent(report: OrderReport, quantity: float) -> OrderIntent:
    """An intent carrying the instrument, side and the given (open/remainder) quantity."""
    return OrderIntent(report.instrument_id, report.side, max(quantity, _EPS))


def _delta_price(prev: OrderReport | None, new: OrderReport) -> float:
    """The price of the fill implied by a filled-quantity delta (delta > 0)."""
    prev_filled = prev.filled_quantity if prev is not None else 0.0
    prev_avg = prev.average_price if prev is not None else 0.0
    delta = new.filled_quantity - prev_filled
    return (new.average_price * new.filled_quantity - prev_avg * prev_filled) / delta


def reduce_order_report(
    prev: OrderReport | None,
    new: OrderReport,
    *,
    currency: Currency | str = Currency.INR,
) -> ReportReduction:
    """Emit the port events between two snapshots of one order (ADR 0019 Appendix A).

    ``prev`` is the previous poll's report, or ``None`` for the first sighting of the order.
    The result is orderd as the venue would have pushed it. Costs are unknown to a query-form
    adapter, so a reduced ``Fill`` carries zero costs (the account books price it later).
    """
    if prev is not None:
        decreasing = new.filled_quantity + _EPS < prev.filled_quantity
        backwards = _RANK[new.status] < _RANK[prev.status]
        if decreasing or backwards:
            return ReportReduction(events=(), flagged=True)

    prev_filled = prev.filled_quantity if prev is not None else 0.0
    prev_status = prev.status if prev is not None else None
    prev_venue = prev.venue_order_id if prev is not None else None
    cur = currency if isinstance(currency, Currency) else Currency(currency)

    events: list[ExecutionEvent] = []

    # 1. Accepted: a first/moved-to working status, or a venue id first seen on a working order.
    moved_to_working = (
        new.status in _WORKING
        and (prev_status is None or prev_status in _PRE_ACK)
        and new.status is not OrderStatus.FILLED
    )
    venue_first_seen = (
        new.venue_order_id is not None
        and prev_venue is None
        and new.status in _WORKING
        and new.status is not OrderStatus.FILLED
    )
    if moved_to_working or venue_first_seen:
        open_qty = new.quantity - new.filled_quantity
        events.append(
            Accepted(
                new.order_id,
                _open_intent(new, open_qty),
                new.ts_event,
                new.venue_order_id,
            )
        )

    # 2. Fill for the quantity delta.
    delta = new.filled_quantity - prev_filled
    if delta > _EPS:
        trade = Trade(
            new.instrument_id,
            new.side,
            delta,
            _delta_price(prev, new),
            new.ts_event,
            new.order_id,
            costs=Money.zero(cur),
        )
        events.append(
            Fill(
                trade,
                new.filled_quantity,
                new.status is OrderStatus.FILLED,
                new.venue_order_id,
            )
        )

    # 3. Terminal event last.
    moved_to_terminal = new.status in _TERMINAL and new.status is not prev_status
    if moved_to_terminal:
        remainder = new.quantity - new.filled_quantity
        intent = _open_intent(new, remainder)
        if new.status is OrderStatus.CANCELLED:
            events.append(Cancelled(new.order_id, intent, new.ts_event, new.venue_order_id))
        elif new.status is OrderStatus.REJECTED:
            events.append(
                Rejected(
                    new.order_id,
                    intent,
                    new.reject_reason or "rejected",
                    new.ts_event,
                    new.venue_order_id,
                )
            )
        else:
            events.append(Expired(new.order_id, intent, new.ts_event, new.venue_order_id))

    return ReportReduction(events=tuple(events))
