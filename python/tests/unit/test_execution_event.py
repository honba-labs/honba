"""ExecutionEvent -> OrderEvent projection (ADR 0019 decision 4), mirroring Rust."""

from __future__ import annotations

import dataclasses

import pytest

from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.order_state import OrderEvent, OrderState
from honba.entities.trade import Trade
from honba.strategies.execution import (
    Accepted,
    Cancelled,
    CancelRequested,
    Expired,
    Fill,
    Rejected,
    Submitted,
    event_order_id,
    order_event,
)

IID = InstrumentId("X", "NSE")
INTENT = OrderIntent.market_buy(IID, 4.0)


def trade(qty: float) -> Trade:
    return Trade(IID, OrderSide.BUY, qty, 10.0, ts=3, order_id="O-1")


def test_projection_of_every_variant() -> None:
    assert order_event(Submitted("O-1", INTENT, 1)) == OrderEvent.submitted(4.0)
    assert order_event(Accepted("O-1", INTENT, 2)) == OrderEvent.accepted()
    assert order_event(Rejected("O-1", INTENT, "rms", 4)) == OrderEvent.rejected()
    assert order_event(Fill(trade(1.0), 1.0, False)) == OrderEvent.fill(1.0, complete=False)
    assert order_event(CancelRequested("O-1", 3)) == OrderEvent.cancel_requested()
    assert order_event(Cancelled("O-1", INTENT, 4)) == OrderEvent.cancelled()
    assert order_event(Expired("O-1", INTENT, 4)) == OrderEvent.expired()


def test_event_order_id() -> None:
    assert event_order_id(Fill(trade(1.0), 1.0, False)) == "O-1"
    assert event_order_id(CancelRequested("O-9", 3)) == "O-9"
    assert event_order_id(Accepted("O-2", INTENT, 2)) == "O-2"


def test_events_are_frozen_and_carry_venue_id() -> None:
    ev = Accepted("O-1", INTENT, 2, venue_order_id="V-1")
    assert ev.venue_order_id == "V-1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.ts = 5  # type: ignore[misc]
    assert Cancelled("O-1", INTENT, 4).venue_order_id is None


def test_fill_without_order_id_is_rejected() -> None:
    bad = Trade(IID, OrderSide.BUY, 1.0, 10.0)
    with pytest.raises(ValueError, match="order_id"):
        event_order_id(Fill(bad, 1.0, False))


def test_flow_through_state() -> None:
    st = OrderState()
    for ev in (
        Submitted("O-1", INTENT, 1),
        Accepted("O-1", INTENT, 2),
        Fill(trade(1.0), 1.0, False),
        Fill(trade(3.0), 4.0, True),
    ):
        st.apply(order_event(ev))
    assert st.filled_qty == 4.0
