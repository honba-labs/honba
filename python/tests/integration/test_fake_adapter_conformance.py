"""E2-S6(c): ``FakeAdapter`` replays the bar-realisable scenarios of ``order_state.json``.

The adapter contract (query form) stays unchanged; this test drives the fake through the
*event form* the kernel speaks (ADR 0019 decision 4) for the scenarios it can realise
via its price-driven matching (``bar_realisable: true`` under the ``l1`` profile).

The FakeAdapter fills instantly on submit (no "accepted" step); its vocabulary maps
directly to the kernel's one-queue order: submit -> fill.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.adapters.models import OrderReport, Product
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind
from honba.domain.order import OrderIntent, OrderSide, OrderType, TimeInForce

EPS = 1e-9
DOC = json.loads(
    (Path(__file__).resolve().parents[3] / "schema" / "conformance" / "order_state.json").read_text(
        encoding="utf-8"
    )
)
# FakeAdapter fills instantly; it maps to the kernel's "l1" profile (no order_accepted).
# We drive only the "submit" steps from the vectors; the fake's instant fill produces
# "order_filled" directly. This tests that the fake's report stream matches the
# normalised l1 expectations of the bar-realisable scenarios.
SCENARIOS = {
    s["name"]: s
    for s in DOC["scenarios"]
    if s.get("bar_realisable", False) and "l1" in s["profile"]
}


def _intent(step: dict[str, Any]) -> OrderIntent:
    return OrderIntent(
        InstrumentId(step["symbol"], "NSE"),
        OrderSide(step["side"]),
        step["quantity"],
        OrderType(step["order_type"]),
        step.get("price"),
        TimeInForce(step["tif"]),
    )


async def _drive(name: str) -> None:
    scenario = SCENARIOS[name]
    X = Instrument(InstrumentId("X", "NSE"), InstrumentKind.EQUITY, lot_size=1.0, tick_size=0.05)
    adapter = FakeAdapter(instruments=(X,))
    await adapter.connect()
    try:
        submitted: set[str] = set()
        reports: dict[str, OrderReport] = {}
        events: list[dict[str, Any]] = []

        async def poll(oid: str) -> None:
            report = await adapter.order_status(oid)
            events.extend(_project(report, submitted, reports.get(oid)))
            reports[oid] = report

        for step in scenario["steps"]:
            op, oid = step["op"], step["order_id"]
            if op == "submit":
                intent = _intent(step)
                await adapter.place_order(intent, product=Product.INTRADAY, client_order_id=oid)
                await poll(oid)
            elif op == "cancel":
                before = (await adapter.order_status(oid)).status if oid in submitted else None
                await adapter.cancel_order(oid)
                if before is not None:
                    await poll(oid)
                else:
                    submitted.discard(oid)
            # venue steps are broker-side events the fake doesn't model; skip them
            elif op == "venue":
                continue
            else:
                raise AssertionError(f"unsupported op {op}")
    finally:
        await adapter.disconnect()

    want = scenario["expect_events"]
    # Normalise for l1 profile: order_accepted is removed from both sides
    l1_want = [e for e in want if e["type"] != "order_accepted"]
    assert len(events) == len(l1_want), (name, events, l1_want)
    for actual, expected in zip(events, l1_want, strict=True):
        assert _matches(actual, expected), (name, actual, expected)
    # Verify final state
    for oid, want_state in scenario["expect_state"].items():
        state = adapter.order_state(oid)
        assert state.status.value == want_state["status"], (name, oid, state)
        assert abs(state.filled_qty - want_state["filled_qty"]) <= EPS, (name, oid, state)
        assert state.cancel_requested == want_state["cancel_requested"], (name, oid, state)


def _project(
    report: OrderReport, submitted: set[str], prev: OrderReport | None
) -> list[dict[str, Any]]:
    """Normalised wire-event dicts per report transition (may be two for instant fills)."""
    oid = report.order_id
    events: list[dict[str, Any]] = []
    if oid not in submitted:
        submitted.add(oid)
        events.append({"type": "order", "order_id": oid, "status": "submitted"})
    if prev is not None and prev.status is report.status:
        return events
    status = report.status.value
    if status == "partially_filled":
        if prev is None:
            raise AssertionError("partial fill with no previous report")
        events.append(
            {
                "type": "order_partially_filled",
                "order_id": oid,
                "last_qty": report.filled_quantity - prev.filled_quantity,
                "cum_qty": report.filled_quantity,
            }
        )
    elif status == "filled":
        if prev is None:
            # Instant fill on first poll: emit the fill alongside the initial "order"
            events.append(
                {
                    "type": "order_filled",
                    "order_id": oid,
                    "last_qty": report.filled_quantity,
                }
            )
        else:
            events.append(
                {
                    "type": "order_filled",
                    "order_id": oid,
                    "last_qty": report.filled_quantity - prev.filled_quantity,
                }
            )
    elif status == "rejected":
        events.append(
            {
                "type": "order_rejected",
                "order_id": oid,
                "quantity": report.quantity - report.filled_quantity,
            }
        )
    elif status == "cancelled":
        events.append(
            {
                "type": "order_cancelled",
                "order_id": oid,
                "quantity": report.quantity - report.filled_quantity,
            }
        )
    elif status == "expired":
        events.append(
            {
                "type": "order_expired",
                "order_id": oid,
                "quantity": report.quantity - report.filled_quantity,
            }
        )
    return events


def _matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, float):
            if got is None or abs(got - want) > EPS:
                return False
        elif got != want:
            return False
    return True


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_fake_adapter_replays_bar_realisable_scenario(name: str) -> None:
    await _drive(name)
