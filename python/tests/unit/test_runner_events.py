"""``StrategyRunner`` over the one ordered event stream (ADR 0019 decision 4).

Mirrors the Rust runner (``crates/honba-strategy/src/runner.rs``): the runner drains
``drain_events()`` once, keeps an ``OrderState`` per order it submitted, books fills and
releases the unfilled remainder of rejected, cancelled and expired orders per instrument.
A duplicate or illegal terminal event releases nothing; queue order is the tiebreak.
"""

from __future__ import annotations

import dataclasses

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderStatus
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.execution import (
    Accepted,
    Cancelled,
    CancelRequested,
    ExecutionEvent,
    Expired,
    Fill,
    Rejected,
)
from honba.strategies.runner import StrategyRunner

A, B = InstrumentId("A", "NSE"), InstrumentId("B", "NSE")


def bar(ts: int, iid: InstrumentId = A) -> Bar:
    return Bar(iid, ts, 10.0, 10.0, 10.0, 10.0, 1.0)


def trade(oid: str, qty: float, iid: InstrumentId = A, ts: int = 1) -> Trade:
    return Trade(iid, OrderSide.BUY, qty, 10.0, ts, oid)


def rest(intent: OrderIntent, qty: float) -> OrderIntent:
    return dataclasses.replace(intent, quantity=qty)


class Buys(Strategy):
    name = "s"

    def __init__(self, *intents: OrderIntent) -> None:
        self.intents = list(intents)
        self.fills: list[Trade] = []

    def on_bar(self, bar: Bar) -> None:
        for intent in self.intents:
            self.ctx.submit(intent)
        self.intents = []

    def on_fill(self, fill: Trade) -> None:
        self.fills.append(fill)


class QueuePort:
    """A new-style duck port: the test pushes events, ``drain_events`` hands them back."""

    def __init__(self) -> None:
        self.queue: list[ExecutionEvent] = []
        self.cancels: list[tuple[str, int]] = []
        self.on_cancel: list[ExecutionEvent] = []
        self.drains = 0

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        pass

    def cancel(self, order_id: str, now: int) -> None:
        self.cancels.append((order_id, now))
        self.queue.extend(self.on_cancel)
        self.on_cancel = []

    def drain_events(self) -> list[ExecutionEvent]:
        self.drains += 1
        out, self.queue = self.queue, []
        return out


def runner_with(*intents: OrderIntent) -> tuple[StrategyRunner, QueuePort, Strategy]:
    port = QueuePort()
    strategy = Buys(*intents)
    runner = StrategyRunner(strategy, port)  # type: ignore[arg-type]
    runner.start()
    runner.on_event(bar(1), 1)  # submits s-0, s-1, ...
    return runner, port, strategy


def test_the_stream_is_drained_once_per_event() -> None:
    runner, port, _ = runner_with(OrderIntent.market_buy(A, 10))
    before = port.drains
    runner.on_event(bar(2), 2)
    assert port.drains == before + 1


def test_runner_keeps_an_order_state_per_order() -> None:
    runner, port, _ = runner_with(OrderIntent.market_buy(A, 10))
    state = runner.order_state("s-0")
    assert state is not None and state.status is OrderStatus.SUBMITTED
    port.queue = [Accepted("s-0", OrderIntent.market_buy(A, 10), 1)]
    runner.on_event(bar(2), 2)
    assert runner.order_state("s-0").status is OrderStatus.ACCEPTED  # type: ignore[union-attr]
    port.queue = [Fill(trade("s-0", 4.0), 4.0, False)]
    runner.on_event(bar(3), 3)
    assert runner.order_state("s-0").status is OrderStatus.PARTIALLY_FILLED  # type: ignore[union-attr]
    port.queue = [Fill(trade("s-0", 6.0), 10.0, True)]
    runner.on_event(bar(4), 4)
    assert runner.order_state("s-0").status is OrderStatus.FILLED  # type: ignore[union-attr]
    assert runner.order_state("nope") is None


def test_release_is_per_instrument_from_the_remainder_the_event_carries() -> None:
    ia, ib = OrderIntent.market_buy(A, 10), OrderIntent.market_buy(B, 5)
    runner, port, _ = runner_with(ia, ib)
    assert runner.ctx.busy(A) and runner.ctx.busy(B)
    port.queue = [
        Fill(trade("s-0", 4.0), 4.0, False),
        Rejected("s-0", rest(ia, 6.0), "no_funds", 2),
        Cancelled("s-1", ib, 2),
    ]
    runner.on_event(bar(2), 2)
    assert runner.released_quantity(A) == 6.0
    assert runner.released_quantity(B) == 5.0
    assert [r.order_id for r in runner.order_rejections] == ["s-0", "s-1"]
    assert [r.cancelled for r in runner.order_rejections] == [False, True]
    assert not runner.ctx.busy(B)
    assert [f.quantity for f in runner.fills] == [4.0]
    # filled + released == ordered, per order
    assert runner.order_state("s-0").status is OrderStatus.REJECTED  # type: ignore[union-attr]
    assert runner.order_state("s-1").status is OrderStatus.CANCELLED  # type: ignore[union-attr]


def test_expired_releases_the_remainder_with_reason_expired() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia)
    port.queue = [Expired("s-0", ia, 5)]
    runner.on_event(bar(2), 2)
    (rejection,) = runner.order_rejections
    assert (rejection.reason, rejection.cancelled) == ("expired", False)
    assert runner.released_quantity(A) == 10.0
    assert not runner.ctx.busy(A)


def test_duplicate_terminal_releases_nothing() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia, OrderIntent.market_buy(A, 3))
    port.queue = [Cancelled("s-0", ia, 2), Cancelled("s-0", ia, 2)]
    runner.on_event(bar(2), 2)
    assert runner.released_quantity(A) == 10.0
    assert len(runner.order_rejections) == 1


def test_illegal_terminal_after_fill_releases_nothing() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia)
    port.queue = [Fill(trade("s-0", 10.0), 10.0, True), Rejected("s-0", ia, "late", 3)]
    runner.on_event(bar(2), 2)
    assert runner.released_quantity(A) == 0.0
    assert runner.order_rejections == []
    assert [f.quantity for f in runner.fills] == [10.0]


def test_a_different_terminal_after_a_terminal_releases_nothing() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia)
    port.queue = [Cancelled("s-0", ia, 2), Rejected("s-0", ia, "late", 2)]
    runner.on_event(bar(2), 2)
    assert runner.released_quantity(A) == 10.0
    assert [r.cancelled for r in runner.order_rejections] == [True]


def test_overfill_is_booked_but_leaves_the_fsm_alone() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia)
    port.queue = [Fill(trade("s-0", 12.0), 12.0, True)]
    runner.on_event(bar(2), 2)
    assert [f.quantity for f in runner.fills] == [12.0]  # money moved: the ledger books it
    assert runner.order_state("s-0").status is OrderStatus.SUBMITTED  # type: ignore[union-attr]


def test_cancel_race_fill_before_cancel_in_queue_order() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia)
    # the venue fills 4 first, then answers the cancel for the remaining 6, at the same ts
    port.on_cancel = [
        Fill(trade("s-0", 4.0, ts=7), 4.0, False),
        CancelRequested("s-0", 7),
        Cancelled("s-0", rest(ia, 6.0), 7),
    ]
    assert runner.cancel("s-0") is True
    assert port.cancels == [("s-0", 1)]  # now = the latest event's ts_init
    assert [f.quantity for f in runner.fills] == [4.0]
    assert runner.released_quantity(A) == 6.0
    assert runner.order_state("s-0").status is OrderStatus.CANCELLED  # type: ignore[union-attr]
    assert runner.order_state("s-0").filled_qty == 4.0  # type: ignore[union-attr]


def test_cancel_race_a_fill_that_wins_leaves_no_cancel_release() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, port, _ = runner_with(ia)
    port.on_cancel = [Fill(trade("s-0", 10.0, ts=7), 10.0, True)]
    runner.cancel("s-0")
    assert runner.order_rejections == []
    assert runner.released_quantity(A) == 0.0
    assert runner.order_state("s-0").status is OrderStatus.FILLED  # type: ignore[union-attr]


def test_cancel_marks_the_pending_cancel_in_the_state() -> None:
    ia = OrderIntent.market_buy(A, 10)
    runner, _, _ = runner_with(ia)
    runner.cancel("s-0")  # the port answers nothing yet
    assert runner.order_state("s-0").cancel_requested is True  # type: ignore[union-attr]


def test_cancel_without_a_cancel_path_returns_false() -> None:
    class NoCancel:
        def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
            pass

        def drain_events(self) -> list[ExecutionEvent]:
            return []

    runner = StrategyRunner(Buys(), NoCancel())  # type: ignore[arg-type]
    assert runner.cancel("x") is False


def test_a_failing_fill_hook_still_releases_the_rest_of_the_stream() -> None:
    ia, ib = OrderIntent.market_buy(A, 10), OrderIntent.market_buy(B, 5)
    runner, port, strategy = runner_with(ia, ib)

    def boom(fill: Trade) -> None:
        raise RuntimeError("hook")

    strategy.on_fill = boom  # type: ignore[method-assign]
    port.queue = [Fill(trade("s-0", 4.0), 4.0, False), Cancelled("s-1", ib, 2)]
    with pytest.raises(RuntimeError, match="hook"):
        runner.on_event(bar(2), 2)
    assert runner.released_quantity(B) == 5.0
    assert [f.quantity for f in runner.fills] == [4.0]
