"""Cross-language vectors for the order-state machine (ADR 0019 decision 8).

Reads ``schema/conformance/order_state.json``; ``crates/honba-entities/tests/order_state_conformance.rs``
reads the same file. At this level (E2-S6(a)) no engine runs: each step is turned into an
``ExecutionEvent``, projected with ``order_event`` and applied to the reference ``OrderState``.
Each op yields at most one wire-event projection (a duplicate no-op yields none), which is
compared to ``expect_events``; ``expect_state`` is compared exactly.

TODO(E2-S6(d)): replay the same scenarios through every gateway (``Scripted``, ``FakeAdapter``,
bar-driven sims) and compare the *normalised* stream per profile (decision 8); that needs the
engines' ``drain_events`` and is out of scope here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderStatus, OrderType, TimeInForce
from honba.entities.order_state import (
    FillMismatch,
    IllegalStatusTransition,
    IllegalTransition,
    InvalidQuantity,
    OrderState,
    Overfill,
)
from honba.entities.trade import Trade
from honba.strategies.execution import (
    Accepted,
    Cancelled,
    CancelRequested,
    ExecutionEvent,
    Expired,
    Fill,
    Rejected,
    Submitted,
    event_order_id,
    order_event,
)

DOC = json.loads(
    (Path(__file__).resolve().parents[3] / "schema" / "conformance" / "order_state.json").read_text(
        encoding="utf-8"
    )
)
EPS = 1e-9
ERRORS: dict[str, type[IllegalTransition]] = {
    "transition": IllegalStatusTransition,
    "overfill": Overfill,
    "fill_mismatch": FillMismatch,
    "invalid_quantity": InvalidQuantity,
}


class Harness:
    """Plays the submitter and the venue: builds ExecutionEvents, applies them, projects."""

    def __init__(self) -> None:
        self.states: dict[str, OrderState] = {}
        self.intents: dict[str, OrderIntent] = {}
        self.events: list[dict[str, Any]] = []

    def _remainder_intent(self, oid: str) -> OrderIntent:
        st, base = self.states[oid], self.intents[oid]
        remainder = st.quantity - st.filled_qty
        return OrderIntent(
            base.instrument_id, base.side, remainder if remainder > 0 else st.quantity
        )

    def step(self, step: dict[str, Any]) -> None:
        op, oid, ts = step["op"], step["order_id"], step["ts"]
        if op == "submit":
            intent = OrderIntent(
                InstrumentId(step["symbol"], "NSE"),
                OrderSide(step["side"]),
                step["quantity"],
                OrderType(step["order_type"]),
                step.get("price"),
                TimeInForce(step["tif"]),
            )
            self.states[oid] = OrderState()
            self.intents[oid] = intent
            self._apply(step, Submitted(oid, intent, ts))
        elif op == "cancel":
            st = self.states.get(oid)
            # Idempotent (ADR 0019 decision 6): unknown, Initialized, terminal or
            # already-requested -> no-op, no event.
            if st is None or st.cancel_requested:
                return
            if st.status not in (
                OrderStatus.SUBMITTED,
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
            ):
                return
            self._apply(step, CancelRequested(oid, ts))
        elif op == "venue":
            self._venue(step)
        else:
            raise AssertionError(f"unsupported op at the state level: {op}")

    def _venue(self, step: dict[str, Any]) -> None:
        kind, oid, ts = step["event"], step["order_id"], step["ts"]
        st = self.states[oid]
        intent = self._remainder_intent(oid) if st.quantity else self.intents[oid]
        if kind == "accepted":
            ev: ExecutionEvent = Accepted(oid, intent, ts)
        elif kind == "rejected":
            ev = Rejected(oid, intent, step.get("reason", "venue"), ts)
        elif kind == "cancelled":
            ev = Cancelled(oid, intent, ts)
        elif kind == "expired":
            ev = Expired(oid, intent, ts)
        elif kind == "fill":
            last = step["last_qty"]
            cum = st.filled_qty + last
            trade = Trade(
                self.intents[oid].instrument_id,
                self.intents[oid].side,
                last,
                step["last_px"],
                ts=ts,
                order_id=oid,
            )
            ev = Fill(trade, cum, cum + EPS >= st.quantity)
        else:
            raise AssertionError(f"unknown venue event {kind}")
        self._apply(step, ev)

    def _apply(self, step: dict[str, Any], ev: ExecutionEvent) -> None:
        oid = event_order_id(ev)
        st = self.states[oid]
        released = st.quantity - st.filled_qty
        expected_error = step.get("expect_error")
        try:
            changed = st.apply(order_event(ev))
        except IllegalTransition as err:
            assert expected_error, f"unexpected {err}"
            assert isinstance(err, ERRORS[expected_error]), f"{expected_error}: got {err!r}"
            return
        assert not expected_error, f"expected {expected_error}, step was legal"
        if changed:
            self.events.append(_project(ev, st, released))


def _project(ev: ExecutionEvent, st: OrderState, released: float) -> dict[str, Any]:
    oid = event_order_id(ev)
    if isinstance(ev, Submitted):
        return {"type": "order", "order_id": oid, "status": "submitted"}
    if isinstance(ev, Accepted):
        return {"type": "order_accepted", "order_id": oid}
    if isinstance(ev, CancelRequested):
        return {"type": "order_cancel_requested", "order_id": oid}
    if isinstance(ev, Fill):
        last = ev.trade.quantity
        if st.status is OrderStatus.FILLED:
            return {"type": "order_filled", "order_id": oid, "last_qty": last}
        return {
            "type": "order_partially_filled",
            "order_id": oid,
            "last_qty": last,
            "cum_qty": st.filled_qty,
        }
    name = {Rejected: "order_rejected", Cancelled: "order_cancelled", Expired: "order_expired"}
    return {"type": name[type(ev)], "order_id": oid, "quantity": released}


def _matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, float):
            if got is None or abs(got - want) > EPS:
                return False
        elif got != want:
            return False
    return True


SCENARIOS = {s["name"]: s for s in DOC["scenarios"]}
REQUIRED = [
    "partial_fill_sequence",
    "reject_remainder_after_partial",
    "cancel_twice_emits_once",
    "duplicate_terminal_is_noop",
    "ioc_remainder_cancelled",
    "cancel_race",
    "market_fill",
    "limit_fill",
    "reject_at_submit",
    "tif_expiry",
]


def test_fixture_header() -> None:
    assert DOC["fixture_version"] == 1
    assert DOC["type"] == "OrderState"
    assert set(DOC["profiles"]) == {"l1", "ack"}
    assert set(REQUIRED) <= set(SCENARIOS)
    assert len(SCENARIOS) == len(DOC["scenarios"])


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_expect_state(name: str) -> None:
    h = Harness()
    for step in SCENARIOS[name]["steps"]:
        h.step(step)
    for oid, want in SCENARIOS[name]["expect_state"].items():
        st = h.states[oid]
        assert st.status.value == want["status"]
        assert abs(st.filled_qty - want["filled_qty"]) <= EPS
        assert st.cancel_requested == want["cancel_requested"]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_expect_events_projection(name: str) -> None:
    """Per-op event projection (not the engine's normalised stream; see module TODO)."""
    h = Harness()
    for step in SCENARIOS[name]["steps"]:
        h.step(step)
    want = SCENARIOS[name]["expect_events"]
    assert len(h.events) == len(want), h.events
    for actual, expected in zip(h.events, want, strict=True):
        assert _matches(actual, expected), (actual, expected)


@pytest.mark.skip(reason="TODO(E2-S6(d)): replay order_state.json through every gateway/profile")
def test_todo_gateway_normalised_stream_replay() -> None:
    raise AssertionError("implemented in E2-S6(d)")
