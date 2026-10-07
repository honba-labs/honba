"""Unit tests for the reference order-state machine (ADR 0019 transition table).

Mirrors ``crates/honba-messages/src/tests/order_state.rs`` cell for cell.
"""

from __future__ import annotations

import math

import pytest

from honba.entities.order import OrderStatus as S
from honba.entities.order_state import (
    FillMismatch,
    IllegalStatusTransition,
    IllegalTransition,
    InvalidQuantity,
    OrderEvent,
    OrderEventKind,
    OrderState,
    Overfill,
)

QTY = 3.0
X, NOOP = "x", "noop"


def to(status: S, cr: bool) -> tuple[S, bool]:
    return (status, cr)


# Columns follow OrderEventKind order: Submitted, Accepted, Rejected, Fill, CancelRequested,
# Cancelled, Expired. Fill is a non-completing 1 of 3.
TABLE = [
    ((S.INITIALIZED, False), [to(S.SUBMITTED, False), X, to(S.REJECTED, False), X, X, X, X]),
    (
        (S.SUBMITTED, False),
        [
            X,
            to(S.ACCEPTED, False),
            to(S.REJECTED, False),
            to(S.PARTIALLY_FILLED, False),
            to(S.SUBMITTED, True),
            to(S.CANCELLED, False),
            to(S.EXPIRED, False),
        ],
    ),
    (
        (S.SUBMITTED, True),
        [
            X,
            to(S.ACCEPTED, True),
            to(S.REJECTED, False),
            to(S.PARTIALLY_FILLED, True),
            NOOP,
            to(S.CANCELLED, False),
            to(S.EXPIRED, False),
        ],
    ),
    (
        (S.ACCEPTED, False),
        [
            X,
            NOOP,
            to(S.REJECTED, False),
            to(S.PARTIALLY_FILLED, False),
            to(S.ACCEPTED, True),
            to(S.CANCELLED, False),
            to(S.EXPIRED, False),
        ],
    ),
    (
        (S.ACCEPTED, True),
        [
            X,
            NOOP,
            to(S.REJECTED, False),
            to(S.PARTIALLY_FILLED, True),
            NOOP,
            to(S.CANCELLED, False),
            to(S.EXPIRED, False),
        ],
    ),
    (
        (S.PARTIALLY_FILLED, False),
        [
            X,
            NOOP,
            to(S.REJECTED, False),
            to(S.PARTIALLY_FILLED, False),
            to(S.PARTIALLY_FILLED, True),
            to(S.CANCELLED, False),
            to(S.EXPIRED, False),
        ],
    ),
    (
        (S.PARTIALLY_FILLED, True),
        [
            X,
            NOOP,
            to(S.REJECTED, False),
            to(S.PARTIALLY_FILLED, True),
            NOOP,
            to(S.CANCELLED, False),
            to(S.EXPIRED, False),
        ],
    ),
    ((S.FILLED, False), [X, X, X, X, X, X, X]),
    ((S.CANCELLED, False), [X, X, X, X, X, NOOP, X]),
    ((S.REJECTED, False), [X, X, NOOP, X, X, X, X]),
    ((S.EXPIRED, False), [X, X, X, X, X, X, NOOP]),
]


def event(kind: OrderEventKind) -> OrderEvent:
    return {
        OrderEventKind.SUBMITTED: OrderEvent.submitted(QTY),
        OrderEventKind.ACCEPTED: OrderEvent.accepted(),
        OrderEventKind.REJECTED: OrderEvent.rejected(),
        OrderEventKind.FILL: OrderEvent.fill(1.0, complete=False),
        OrderEventKind.CANCEL_REQUESTED: OrderEvent.cancel_requested(),
        OrderEventKind.CANCELLED: OrderEvent.cancelled(),
        OrderEventKind.EXPIRED: OrderEvent.expired(),
    }[kind]


def state_at(status: S, cr: bool) -> OrderState:
    if status is S.INITIALIZED:
        quantity, filled = 0.0, 0.0
    elif status is S.PARTIALLY_FILLED:
        quantity, filled = QTY, 1.0
    elif status is S.FILLED:
        quantity, filled = QTY, QTY
    else:
        quantity, filled = QTY, 0.0
    return OrderState(status, quantity, filled, cr)


def snapshot(s: OrderState) -> tuple[S, float, float, bool]:
    return (s.status, s.quantity, s.filled_qty, s.cancel_requested)


def test_event_kinds_are_seven() -> None:
    assert len(list(OrderEventKind)) == 7
    assert len(TABLE) == 11


@pytest.mark.parametrize(("frm", "cells"), TABLE, ids=lambda v: str(v))
def test_transition_table_exhaustive(frm: tuple[S, bool], cells: list[object]) -> None:
    for kind, cell in zip(OrderEventKind, cells, strict=True):
        s = state_at(*frm)
        before = snapshot(s)
        ctx = f"{frm} x {kind}"
        if cell == X:
            with pytest.raises(IllegalTransition):
                s.apply(event(kind))
            assert snapshot(s) == before, ctx
            assert not OrderState.can_transition(frm, kind), ctx
        elif cell == NOOP:
            assert s.apply(event(kind)) is False, ctx
            assert snapshot(s) == before, ctx
            assert OrderState.can_transition(frm, kind), ctx
        else:
            assert s.apply(event(kind)) is True, ctx
            assert (s.status, s.cancel_requested) == cell, ctx
            assert OrderState.can_transition(frm, kind), ctx


def test_illegal_transition_is_typed_with_stable_prose() -> None:
    s = OrderState()
    with pytest.raises(IllegalStatusTransition) as ei:
        s.apply(OrderEvent.accepted())
    err = ei.value
    assert isinstance(err, IllegalTransition)
    assert (err.status, err.cancel_requested, err.event) == (
        S.INITIALIZED,
        False,
        OrderEventKind.ACCEPTED,
    )
    assert str(err) == (
        "illegal transition: accepted in status Initialized (cancel_requested=false)"
    )


def test_new_state_is_initialized_and_empty() -> None:
    s = OrderState()
    assert snapshot(s) == (S.INITIALIZED, 0.0, 0.0, False)
    assert s == OrderState()


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_submitted_validates_quantity(bad: float) -> None:
    s = OrderState()
    with pytest.raises(InvalidQuantity):
        s.apply(OrderEvent.submitted(bad))
    assert s == OrderState()


def test_submitted_sets_quantity() -> None:
    s = OrderState()
    assert s.apply(OrderEvent.submitted(5.0)) is True
    assert s.quantity == 5.0


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan])
def test_fill_validates_last_qty(bad: float) -> None:
    s = state_at(S.ACCEPTED, False)
    with pytest.raises(InvalidQuantity):
        s.apply(OrderEvent.fill(bad, complete=False))
    assert snapshot(s) == snapshot(state_at(S.ACCEPTED, False))


def test_terminal_is_final() -> None:
    for terminal in (S.FILLED, S.CANCELLED, S.REJECTED, S.EXPIRED):
        for kind in OrderEventKind:
            s = state_at(terminal, False)
            before = snapshot(s)
            try:
                r = s.apply(event(kind))
            except IllegalTransition:
                r = None
            assert r in (None, False), (terminal, kind)
            assert snapshot(s) == before


def test_duplicate_terminal_is_noop() -> None:
    for status, ev in [
        (S.CANCELLED, OrderEvent.cancelled()),
        (S.REJECTED, OrderEvent.rejected()),
        (S.EXPIRED, OrderEvent.expired()),
    ]:
        s = state_at(status, False)
        assert s.apply(ev) is False
        assert s.status is status
    s = state_at(S.FILLED, False)
    with pytest.raises(Overfill):
        s.apply(OrderEvent.fill(1.0, complete=True))


def test_overfill_rejected_and_tolerance() -> None:
    s = state_at(S.ACCEPTED, False)
    with pytest.raises(Overfill) as ei:
        s.apply(OrderEvent.fill(3.5, complete=True))
    assert (ei.value.quantity, ei.value.filled_qty, ei.value.last_qty) == (3.0, 0.0, 3.5)
    assert snapshot(s) == snapshot(state_at(S.ACCEPTED, False))
    # Within 1e-9 is not an overfill.
    assert s.apply(OrderEvent.fill(3.0 + 5e-10, complete=True)) is True
    assert s.status is S.FILLED


def test_fill_mismatch_rejected() -> None:
    s = state_at(S.ACCEPTED, False)
    with pytest.raises(FillMismatch) as ei:
        s.apply(OrderEvent.fill(1.0, complete=True))
    assert (ei.value.claimed_complete, ei.value.derived_complete) == (True, False)
    with pytest.raises(FillMismatch) as ei:
        s.apply(OrderEvent.fill(3.0, complete=False))
    assert (ei.value.claimed_complete, ei.value.derived_complete) == (False, True)
    assert snapshot(s) == snapshot(state_at(S.ACCEPTED, False))


def test_check_order_legality_then_quantity_then_overfill_then_mismatch() -> None:
    # Legality first: Fill in Initialized is a Transition error even with a bad quantity.
    with pytest.raises(IllegalStatusTransition):
        OrderState().apply(OrderEvent.fill(-1.0, complete=True))
    # Quantity before overfill.
    with pytest.raises(InvalidQuantity):
        state_at(S.ACCEPTED, False).apply(OrderEvent.fill(math.nan, complete=True))
    # Overfill before mismatch (overfill claims complete=False).
    with pytest.raises(Overfill):
        state_at(S.ACCEPTED, False).apply(OrderEvent.fill(4.0, complete=False))


def test_partial_fill_sequence() -> None:
    s = OrderState()
    s.apply(OrderEvent.submitted(3.0))
    s.apply(OrderEvent.accepted())
    s.apply(OrderEvent.fill(1.0, complete=False))
    assert (s.status, s.filled_qty) == (S.PARTIALLY_FILLED, 1.0)
    s.apply(OrderEvent.fill(2.0, complete=True))
    assert (s.status, s.filled_qty) == (S.FILLED, 3.0)


def test_cancel_requested_cleared_on_terminal() -> None:
    for terminal in (
        OrderEvent.rejected(),
        OrderEvent.cancelled(),
        OrderEvent.expired(),
        OrderEvent.fill(2.0, complete=True),
    ):
        s = state_at(S.PARTIALLY_FILLED, True)
        s.apply(terminal)
        assert s.cancel_requested is False


def test_expired_transitions() -> None:
    for status in (S.SUBMITTED, S.ACCEPTED, S.PARTIALLY_FILLED):
        s = state_at(status, False)
        assert s.apply(OrderEvent.expired()) is True
        assert s.status is S.EXPIRED
    with pytest.raises(IllegalTransition):
        OrderState().apply(OrderEvent.expired())
    with pytest.raises(IllegalTransition):
        state_at(S.FILLED, False).apply(OrderEvent.expired())
