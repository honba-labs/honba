"""The ``ExecutionPort`` shims of ADR 0019 decision 4 (E2-S6b chunk 3).

A new port drains one ordered event stream (``drain_events``) and cancels with
``cancel(order_id, now)``. Until 0.3.0 two shims keep the old shape alive:

* new port, legacy caller: ``drain_fills`` / ``drain_rejections`` are a buffered split of
  one ``drain_events`` call, so no event is lost whichever drain is called first;
* legacy port, new runner: ``drain_fills`` / ``drain_rejections`` are wrapped into events,
  and a one-argument ``cancel(order_id)`` is adapted by ``inspect.signature``.
"""

from __future__ import annotations

import warnings

import pytest

from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.execution import (
    Accepted,
    BaseExecutionPort,
    Cancelled,
    CancelRequested,
    ExecutionEvent,
    Expired,
    Fill,
    LegacyPortEvents,
    OrderRejection,
    Rejected,
    Submitted,
    adapt_port,
    cancel_order,
)

X = InstrumentId("X", "NSE")
INTENT = OrderIntent.market_buy(X, 10)


def trade(oid: str, qty: float, ts: int = 1) -> Trade:
    return Trade(X, OrderSide.BUY, qty, 10.0, ts, oid)


class EventPort(BaseExecutionPort):
    """A new-style port: scripted events, ``cancel(order_id, now)``."""

    def __init__(self, events: list[ExecutionEvent]) -> None:
        self.events = events
        self.drains = 0
        self.cancels: list[tuple[str, int]] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        pass

    def drain_events(self) -> list[ExecutionEvent]:
        self.drains += 1
        out, self.events = self.events, []
        return out

    def cancel(self, order_id: str, now: int) -> None:
        self.cancels.append((order_id, now))


MIXED: list[ExecutionEvent] = [
    Submitted("o-0", INTENT, 1),
    Accepted("o-0", INTENT, 1),
    Fill(trade("o-0", 4.0), 4.0, False),
    Rejected("o-0", OrderIntent.market_buy(X, 6), "no_funds", 2),
    Fill(trade("o-1", 10.0, 3), 10.0, True),
    CancelRequested("o-2", 3),
    Cancelled("o-2", INTENT, 3),
    Expired("o-3", OrderIntent.market_buy(X, 2), 4),
]


def test_buffered_shim_loses_no_event() -> None:
    port = EventPort(list(MIXED))
    fills = port.drain_fills()
    assert [f.order_id for f in fills] == ["o-0", "o-1"]
    # the rejections were buffered by the same drain_events call, not dropped
    rejections = port.drain_rejections()
    assert port.drains == 2  # each legacy call pumps drain_events once (ADR 0019)
    assert [(r.order_id, r.reason, r.cancelled) for r in rejections] == [
        ("o-0", "no_funds", False),
        ("o-2", "cancelled", True),
        ("o-3", "expired", False),
    ]
    assert rejections[0].intent.quantity == 6
    assert rejections[0].ts == 2
    assert all(isinstance(r, OrderRejection) for r in rejections)


def test_buffered_shim_other_order_and_clears_only_its_own_buffer() -> None:
    port = EventPort(list(MIXED))
    assert len(port.drain_rejections()) == 3
    assert len(port.drain_fills()) == 2  # still buffered after the other drain
    assert port.drain_fills() == [] and port.drain_rejections() == []
    port.events = [Fill(trade("o-9", 1.0), 1.0, True)]
    assert [f.order_id for f in port.drain_fills()] == ["o-9"]  # pumps again once empty


def test_buffered_shim_pumps_per_call_and_keeps_new_events() -> None:
    port = EventPort([Fill(trade("o-0", 1.0), 1.0, True)])
    assert len(port.drain_fills()) == 1
    port.events = [Rejected("o-1", INTENT, "r", 5)]
    assert port.drain_fills() == []
    assert [r.order_id for r in port.drain_rejections()] == ["o-1"]


def test_base_port_with_no_drains_is_inert() -> None:
    class Inert(BaseExecutionPort):
        def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
            pass

    port = Inert()
    assert port.drain_events() == []
    assert port.drain_fills() == [] and port.drain_rejections() == []
    port.cancel("nothing", 0)  # inert default, two-argument form


class LegacyPort(BaseExecutionPort):
    """A port from before the event stream: two drains and a one-argument cancel."""

    def __init__(self) -> None:
        self.fills: list[Trade] = []
        self.rejections: list[OrderRejection] = []
        self.cancelled: list[str] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        pass

    def drain_fills(self) -> list[Trade]:
        out, self.fills = self.fills, []
        return out

    def drain_rejections(self) -> list[OrderRejection]:
        out, self.rejections = self.rejections, []
        return out

    def cancel(self, order_id: str) -> None:
        self.cancelled.append(order_id)


def test_legacy_port_events_are_fills_then_rejections_with_derived_cum_qty() -> None:
    port = LegacyPort()
    wrapped = LegacyPortEvents(port)
    wrapped.submit("o-0", INTENT, 1)
    port.fills = [trade("o-0", 4.0), trade("o-0", 6.0, 2)]
    port.rejections = [
        OrderRejection("o-7", INTENT, "cancelled", 5, cancelled=True),
        OrderRejection("o-8", INTENT, "no_funds", 6),
    ]
    got = wrapped.drain_events()
    kinds = [type(e).__name__ for e in got]
    assert kinds == ["Fill", "Fill", "Cancelled", "Rejected"]  # documented legacy order
    assert isinstance(got[0], Fill) and isinstance(got[1], Fill)
    assert (got[0].cum_qty, got[0].complete) == (4.0, False)
    assert (got[1].cum_qty, got[1].complete) == (10.0, True)
    assert isinstance(got[3], Rejected) and got[3].reason == "no_funds"


def test_legacy_expired_reason_maps_back_to_expired() -> None:
    port = LegacyPort()
    port.rejections = [OrderRejection("o-1", INTENT, "expired", 5)]
    got = LegacyPortEvents(port).drain_events()
    assert isinstance(got[0], Rejected)  # a legacy port cannot say Expired: it stays a reject


def test_legacy_cancel_signature_adapted() -> None:
    legacy = LegacyPort()
    with pytest.warns(DeprecationWarning, match="cancel"):
        assert cancel_order(legacy, "o-1", 99) is True
    assert legacy.cancelled == ["o-1"]

    modern = EventPort([])
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the two-argument form is not deprecated
        assert cancel_order(modern, "o-2", 77) is True
    assert modern.cancels == [("o-2", 77)]


def test_adapt_port_warns_once_for_a_one_argument_cancel() -> None:
    legacy = LegacyPort()
    with pytest.warns(DeprecationWarning, match="cancel"):
        adapted = adapt_port(legacy)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert adapted.cancel("o-1", 5) is True
        assert adapted.cancel("o-2", 6) is True
    assert legacy.cancelled == ["o-1", "o-2"]


def test_adapt_port_without_cancel_reports_false() -> None:
    class NoCancel:
        def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
            pass

        def drain_fills(self) -> list[Trade]:
            return []

    adapted = adapt_port(NoCancel())
    assert adapted.cancel("o-1", 1) is False
    assert adapted.drain_events() == []
    assert cancel_order(NoCancel(), "o-1") is False


def test_adapt_port_passes_an_event_port_through() -> None:
    port = EventPort([Rejected("o-0", INTENT, "r", 1)])
    adapted = adapt_port(port)
    assert [type(e).__name__ for e in adapted.drain_events()] == ["Rejected"]
    assert adapted.cancel("o-0", 9) is True
    assert port.cancels == [("o-0", 9)]
