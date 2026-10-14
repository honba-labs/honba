"""Cross-language vectors for the order-state machine (ADR 0019 decision 8).

Reads ``schema/conformance/order_state.json``; ``crates/honba-entities/tests/order_state_conformance.rs``
reads the same file. At this level (E2-S6(a)) no engine runs: each step is turned into an
``ExecutionEvent``, projected with ``order_event`` and applied to the reference ``OrderState``.
Each op yields at most one wire-event projection (a duplicate no-op yields none), which is
compared to ``expect_events``; ``expect_state`` is compared exactly.

E2-S6(d) replays the same scenarios through the Python and native gateways (``NextOpenExecution``
on both backends, ``FakeAdapter``) and compares the *normalised* stream per profile (decision 8):
under the ``l1`` profile ``order_accepted`` is removed from both sides, and only the keys a
scenario lists (type, order_id, quantity fields) are compared.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.adapters.models import OrderReport, Product
from honba.adapters.order_events import reduce_order_report
from honba.adapters.testing import FakeAdapter
from honba.backtest.simulated import NextOpenExecution
from honba.domain.instrument import Instrument, InstrumentKind
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
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


# --- E2-S6(d): every gateway runs the scenario set under its declared profile ---------------


def _gateway_intent(step: dict[str, Any]) -> OrderIntent:
    return OrderIntent(
        InstrumentId(step["symbol"], "NSE"),
        OrderSide(step["side"]),
        step["quantity"],
        OrderType(step["order_type"]),
        step.get("price"),
        TimeInForce(step["tif"]),
    )


def _normalise(events: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    if profile == "l1":
        return [e for e in events if e["type"] != "order_accepted"]
    return events


class _GatewayPlay:
    """Drives one gateway and projects its drained events against the reference FSM."""

    def __init__(self) -> None:
        self.states: dict[str, OrderState] = {}
        self.intents: dict[str, OrderIntent] = {}
        self.events: list[dict[str, Any]] = []

    def begin(self, oid: str, intent: OrderIntent, ts: int) -> None:
        self.states[oid] = OrderState()
        self.intents[oid] = intent
        self.absorb(Submitted(oid, intent, ts))

    def cancel(self, oid: str, ts: int) -> bool:
        st = self.states.get(oid)
        if st is None or st.cancel_requested:
            return False
        if st.status not in (
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            return False
        self.absorb(CancelRequested(oid, ts))
        return True

    def absorb(self, ev: ExecutionEvent) -> None:
        oid = event_order_id(ev)
        st = self.states[oid]
        released = st.quantity - st.filled_qty
        # A flagged reduction (E2-S7) never reaches the ordered stream; the harness
        # asserts not-flagged for the reducer path, so here we expect a legal apply.
        if st.apply(order_event(ev)):
            self.events.append(_project(ev, st, released))

    def absorb_many(self, evs: list[ExecutionEvent]) -> None:
        for ev in evs:
            self.absorb(ev)


def _bar(symbol: str, ts: int, price: float) -> Bar:
    return Bar(InstrumentId(symbol, "NSE"), ts, price, price, price, price, 1000.0)


def _run_next_open(scenario: dict[str, Any], backend: str) -> _GatewayPlay:
    """A price-driven L1 sim: submit fills at the next session's open (market orders only)."""
    port = NextOpenExecution(
        cash=Money.from_major(1_000_000.0, Currency.INR),
        backend=backend,  # type: ignore[arg-type]
    )
    play = _GatewayPlay()
    for step in scenario["steps"]:
        op = step["op"]
        if op == "submit":
            intent = _gateway_intent(step)
            ts = step["ts"]
            port.open_session(ts, [_bar(step["symbol"], ts, 10.0)])
            play.begin(step["order_id"], intent, ts)
            port.submit(step["order_id"], intent, ts)
        elif op == "cancel":
            if play.cancel(step["order_id"], step["ts"]):
                port.cancel(step["order_id"], step["ts"])
        elif op == "venue" and step["event"] == "fill":
            iid = play.intents[step["order_id"]].instrument_id
            ts = step["ts"] + 1
            port.open_session(
                ts,
                [
                    Bar(
                        iid,
                        ts,
                        step["last_px"],
                        step["last_px"],
                        step["last_px"],
                        step["last_px"],
                        1000.0,
                    )
                ],
            )
            play.absorb_many(port.drain_events())
        else:
            raise AssertionError(f"next_open cannot realise op {op}/{step.get('event')}")
    return play


async def _run_fake(scenario: dict[str, Any]) -> _GatewayPlay:
    """The query-form FakeAdapter, reduced to the event stream by reduce_order_report."""
    x = Instrument(InstrumentId("X", "NSE"), InstrumentKind.EQUITY, lot_size=1.0, tick_size=0.05)
    adapter = FakeAdapter(instruments=(x,))
    await adapter.connect()
    play = _GatewayPlay()
    prev: dict[str, OrderReport] = {}
    try:
        for step in scenario["steps"]:
            op = step["op"]
            if op == "submit":
                intent = _gateway_intent(step)
                oid = step["order_id"]
                play.begin(oid, intent, step["ts"])
                await adapter.place_order(intent, product=Product.INTRADAY, client_order_id=oid)
                report = await adapter.order_status(oid)
                reduction = reduce_order_report(prev.get(oid), report)
                assert not reduction.flagged, (scenario["name"], report)
                play.absorb_many(list(reduction.events))
                prev[oid] = report
            elif op == "venue":
                # Polling adapter realises the venue side through the snapshot diff.
                continue
            else:
                raise AssertionError(f"fake cannot realise op {op}")
    finally:
        await adapter.disconnect()
    return play


def _assert_gateway(scenario: dict[str, Any], profile: str, play: _GatewayPlay) -> None:
    want = _normalise(scenario["expect_events"], profile)
    got = _normalise(play.events, profile)
    name = scenario["name"]
    assert len(got) == len(want), f"{name}: got={got} vs want={want}"
    for actual, expected in zip(got, want, strict=True):
        assert _matches(actual, expected), (name, actual, expected)
    for oid, want_state in scenario["expect_state"].items():
        st = play.states[oid]
        assert st.status.value == want_state["status"], (name, oid, st)
        assert abs(st.filled_qty - want_state["filled_qty"]) <= EPS, (name, oid, st)
        assert st.cancel_requested == want_state["cancel_requested"], (name, oid, st)


#: (gateway, scenario) pairs a gateway can realise from the vector's steps, per the ADR
#: profile gate (l1 never emits accepted; the fake's poll collapses accepted into l1).
GATEWAY_CASES = [
    ("next_open_python", "market_fill", "python"),
    ("next_open_native", "market_fill", "native"),
    ("fake", "market_fill", None),
    ("fake", "limit_fill", None),
]


@pytest.mark.parametrize(
    ("gateway", "scenario_name", "backend"),
    GATEWAY_CASES,
    ids=[f"{c[0]}:{c[1]}" for c in GATEWAY_CASES],
)
async def test_gateway_normalised_stream_replay(
    gateway: str, scenario_name: str, backend: str | None
) -> None:
    scenario = SCENARIOS[scenario_name]
    if gateway.startswith("next_open"):
        if backend == "native":
            pytest.importorskip("honba._honba")
        play = _run_next_open(scenario, backend or "python")
    else:
        play = await _run_fake(scenario)
    _assert_gateway(scenario, "l1", play)
