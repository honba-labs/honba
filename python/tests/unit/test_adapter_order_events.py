"""Unit tests for the query-form snapshot-diff reducer (ADR 0019 Appendix A, E2-S6(c)).

The reducer turns two consecutive ``OrderReport`` polls into the ordered port-event stream the
kernel's one-queue vocabulary uses. These are the ``snapshot_reducer_*`` cases the ADR names:
monotone fills, the terminal event last, and a decreasing fill (or a backwards status) flagged
for reconciliation.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from honba.adapters.models import OrderReport, Product
from honba.adapters.order_events import ReportReduction, reduce_order_report
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce
from honba.strategies.execution import Accepted, Cancelled, Expired, Fill, Rejected

IID = InstrumentId("RELIANCE", "NSE")
EPS = 1e-9


def report(**over: object) -> OrderReport:
    base: dict[str, object] = {
        "order_id": "O-1",
        "instrument_id": IID,
        "side": OrderSide.BUY,
        "quantity": 10.0,
        "status": OrderStatus.SUBMITTED,
        "product": Product.INTRADAY,
        "order_type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
        "filled_quantity": 0.0,
        "average_price": 0.0,
        "ts_event": 1,
        "venue_order_id": None,
    }
    base.update(over)
    return OrderReport(**base)  # type: ignore[arg-type]


def test_snapshot_reducer_emits_accepted_on_the_move_to_a_working_status() -> None:
    r = reduce_order_report(
        report(status=OrderStatus.SUBMITTED), report(status=OrderStatus.ACCEPTED)
    )
    assert r.flagged is False
    assert len(r.events) == 1
    (ev,) = r.events
    assert isinstance(ev, Accepted)
    assert ev.order_id == "O-1"
    assert ev.intent.instrument_id == IID and ev.intent.side is OrderSide.BUY
    assert ev.intent.quantity == 10.0  # the whole order is open


def test_snapshot_reducer_monotone_fills() -> None:
    accepted = report(status=OrderStatus.ACCEPTED)
    partial = report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=4.0, average_price=10.0)
    filled = report(status=OrderStatus.FILLED, filled_quantity=10.0, average_price=11.0)

    (first,) = reduce_order_report(None, accepted).events
    assert isinstance(first, Accepted)

    (second,) = reduce_order_report(accepted, partial).events
    assert isinstance(second, Fill)
    assert (second.trade.quantity, second.cum_qty, second.complete) == (4.0, 4.0, False)

    (third,) = reduce_order_report(partial, filled).events
    assert isinstance(third, Fill)
    assert (third.trade.quantity, third.cum_qty, third.complete) == (6.0, 10.0, True)


def test_snapshot_reducer_fill_price_is_the_delta_weighted_average() -> None:
    prev = report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=4.0, average_price=10.0)
    new = report(status=OrderStatus.FILLED, filled_quantity=10.0, average_price=11.0)
    (fill,) = reduce_order_report(prev, new).events
    assert isinstance(fill, Fill)
    # (11 * 10 - 10 * 4) / 6 = 70 / 6
    assert abs(fill.trade.price - 70.0 / 6.0) <= EPS


def test_snapshot_reducer_puts_the_terminal_event_last() -> None:
    prev = report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=4.0, average_price=10.0)
    # A last fill and the cancellation that follows it, in one poll.
    cancelled = report(
        status=OrderStatus.CANCELLED, filled_quantity=6.0, average_price=10.0, ts_event=5
    )
    events = reduce_order_report(prev, cancelled).events
    assert [type(e) for e in events] == [Fill, Cancelled]
    terminal = events[-1]
    assert isinstance(terminal, Cancelled)
    assert terminal.intent.quantity == 4.0  # the unfilled remainder (10 - 6)


def test_snapshot_reducer_terminal_without_a_fill_emits_only_the_terminal() -> None:
    prev = report(status=OrderStatus.ACCEPTED)
    rejected = report(status=OrderStatus.REJECTED, reject_reason="rms", ts_event=3)
    (ev,) = reduce_order_report(prev, rejected).events
    assert isinstance(ev, Rejected)
    assert ev.reason == "rms" and ev.intent.quantity == 10.0


def test_snapshot_reducer_expired_is_terminal() -> None:
    prev = report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=4.0, average_price=10.0)
    expired = report(status=OrderStatus.EXPIRED, filled_quantity=4.0, average_price=10.0)
    (ev,) = reduce_order_report(prev, expired).events
    assert isinstance(ev, Expired)
    assert ev.intent.quantity == 6.0


def test_snapshot_reducer_decreasing_fill_is_flagged() -> None:
    prev = report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=6.0, average_price=10.0)
    new = report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=4.0, average_price=10.0)
    r = reduce_order_report(prev, new)
    assert r.events == ()
    assert r.flagged is True


def test_snapshot_reducer_backwards_status_is_flagged() -> None:
    prev = report(status=OrderStatus.ACCEPTED)
    new = report(status=OrderStatus.SUBMITTED)
    r = reduce_order_report(prev, new)
    assert r.events == ()
    assert r.flagged is True


def test_snapshot_reducer_venue_id_first_seen_emits_accepted() -> None:
    prev = report(status=OrderStatus.ACCEPTED, venue_order_id=None)
    new = report(status=OrderStatus.ACCEPTED, venue_order_id="V-9")
    (ev,) = reduce_order_report(prev, new).events
    assert isinstance(ev, Accepted)
    assert ev.venue_order_id == "V-9"


def test_snapshot_reducer_unchanged_snapshot_emits_nothing() -> None:
    prev = report(status=OrderStatus.ACCEPTED, venue_order_id="V-9")
    new = report(status=OrderStatus.ACCEPTED, venue_order_id="V-9")
    assert reduce_order_report(prev, new) == ReportReduction(events=())


def test_snapshot_reducer_shape_is_a_frozen_result() -> None:
    assert ReportReduction(events=()).flagged is False
    with pytest.raises(FrozenInstanceError):
        ReportReduction(events=()).events = ()  # type: ignore[misc]
