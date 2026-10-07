"""``honba._honba.NextOpenSimulator.drain_events`` (ADR 0019 decision 4, E2-S6(b) b2).

One ordered stream replaces the two drains; the legacy pair stays as a buffered shim until
0.3.0. The conversion to ``ExecutionEvent`` dataclasses happens in ``events_from_native``.
"""

from __future__ import annotations

from typing import Any

import pytest

from honba.strategies.execution import Cancelled, Fill, Rejected, events_from_native

_honba = pytest.importorskip("honba._honba")
if not hasattr(_honba.NextOpenSimulator, "drain_events"):  # pragma: no cover - stale extension
    pytest.fail("honba._honba lacks NextOpenSimulator.drain_events: rebuild the extension")


def _bar(ts: int, open_: float) -> dict[str, Any]:
    return {
        "symbol": "AAA",
        "ts": ts,
        "open": open_,
        "high": open_,
        "low": open_,
        "close": open_,
        "volume": 1000.0,
    }


def _order(oid: str, side: str, qty: float) -> dict[str, Any]:
    return {"id": oid, "symbol": "AAA", "side": side, "qty": qty, "ts": 1}


def _sim() -> Any:
    sim = _honba.NextOpenSimulator(1_000_000)
    sim.on_bar(_bar(1, 100.0))
    return sim


def test_drain_events_returns_tagged_dicts_in_production_order() -> None:
    sim = _sim()
    sim.submit(_order("o-0", "buy", 10.0))
    sim.submit(_order("o-1", "sell", 99.0))  # no position: refused at submit
    sim.on_bar(_bar(2, 110.0))
    raw = sim.drain_events()
    assert [e["kind"] for e in raw] == ["rejected", "fill"]
    assert raw[0]["reason"] == "no_position"
    assert (raw[1]["price"], raw[1]["cum_qty"], raw[1]["complete"]) == (110.0, 10.0, True)
    assert sim.drain_events() == []


def test_events_convert_to_the_python_dataclasses() -> None:
    sim = _sim()
    sim.submit(_order("o-0", "buy", 1.0))
    sim.cancel("o-0", 7)
    sim.submit(_order("o-1", "sell", 5.0))
    sim.submit(_order("o-2", "buy", 2.0))
    sim.on_bar(_bar(2, 100.0))
    events = events_from_native(sim.drain_events())
    assert [type(e) for e in events] == [Cancelled, Rejected, Fill]
    assert events[0].ts == 7 and events[0].intent.quantity == 1.0
    assert events[2].trade.order_id == "o-2"


def test_legacy_drains_are_still_served_beside_the_event_drain() -> None:
    sim = _sim()
    sim.submit(_order("o-0", "buy", 1.0))
    sim.cancel("o-0", 1)
    sim.submit(_order("o-1", "buy", 2.0))
    sim.on_bar(_bar(2, 100.0))
    assert len(sim.drain_rejections()) == 1
    assert len(sim.drain_fills()) == 1
