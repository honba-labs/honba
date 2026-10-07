"""``StrategyRunner`` + a scripted event port through the real flow (ADR 0019, E2-S6b b3).

A scripted venue emits ``ExecutionEvent``s on its own schedule; the runner, the real
``LedgerContext`` and the strategy do the rest. Each scenario ends with the invariant
``filled + released == ordered`` and an idle context.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderStatus
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.execution import (
    Accepted,
    BaseExecutionPort,
    Cancelled,
    CancelRequested,
    ExecutionEvent,
    Expired,
    Fill,
    Rejected,
)
from honba.strategies.runner import StrategyRunner

X = InstrumentId("X", "NSE")
PX = 10.0


class BuyOnce(Strategy):
    name = "s"

    def __init__(self, qty: float) -> None:
        self.qty = qty
        self.sent = False

    def on_bar(self, bar: Bar) -> None:
        if not self.sent:
            self.sent = True
            self.ctx.submit(OrderIntent.market_buy(X, self.qty))


class Venue(BaseExecutionPort):
    """Emits the scripted events of its order at each bar ``ts`` (queue order = list order)."""

    def __init__(self, order_qty: float, script: dict[int, list[str | tuple[str, float]]]) -> None:
        self.qty = order_qty
        self.script = script
        self.filled = 0.0
        self.now = 0
        self.queue: list[ExecutionEvent] = []
        self.cancel_at: list[int] = []

    def on_event(self, event: Any, ts_init: int) -> None:
        self.now = ts_init
        for step in self.script.get(ts_init, []):
            self.queue.extend(self._step(step, ts_init))

    def _intent(self, qty: float) -> OrderIntent:
        return dataclasses.replace(OrderIntent.market_buy(X, self.qty), quantity=qty)

    def _step(self, step: str | tuple[str, float], ts: int) -> list[ExecutionEvent]:
        kind, qty = (step, 0.0) if isinstance(step, str) else step
        left = self.qty - self.filled or self.qty  # a late event after the completing fill
        if kind == "accept":
            return [Accepted("s-0", self._intent(left), ts, "V-1")]
        if kind == "fill":
            self.filled += qty
            done = self.filled >= self.qty
            trade = Trade(X, OrderSide.BUY, qty, PX, ts, "s-0")
            return [Fill(trade, self.filled, done, "V-1")]
        if kind == "reject":
            return [Rejected("s-0", self._intent(left), "venue_rms", ts, "V-1")]
        if kind == "expire":
            return [Expired("s-0", self._intent(left), ts, "V-1")]
        if kind == "cancel":
            return [Cancelled("s-0", self._intent(left), ts, "V-1")]
        raise AssertionError(kind)

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        pass

    def cancel(self, order_id: str, now: int) -> None:
        self.cancel_at.append(now)
        self.queue.append(CancelRequested(order_id, now))
        for step in self.script.get(-1, []):  # the venue's answer to the cancel, at `now`
            self.queue.extend(self._step(step, now))

    def drain_events(self) -> list[ExecutionEvent]:
        out, self.queue = self.queue, []
        return out


def run(
    qty: float,
    script: dict[int, list[str | tuple[str, float]]],
    cancel_after: int | None = None,
) -> tuple[StrategyRunner, Venue]:
    venue = Venue(qty, script)
    runner = StrategyRunner(BuyOnce(qty), venue)
    runner.start()
    for ts in range(1, 6):
        venue.on_event(None, ts)
        runner.on_event(Bar(X, ts, PX, PX, PX, PX, 1.0), ts)
        if cancel_after == ts:
            runner.cancel("s-0")
    runner.stop()
    return runner, venue


def accounted(runner: StrategyRunner) -> None:
    done: dict[str, float] = defaultdict(float)
    for f in runner.fills:
        done[f.order_id] += f.quantity
    for r in runner.order_rejections:
        done[r.order_id] += r.intent.quantity
    assert done["s-0"] == 10.0
    assert not runner.ctx.busy(X)


def test_submit_accept_partial_then_fill() -> None:
    runner, _ = run(10.0, {2: ["accept"], 3: [("fill", 4.0)], 4: [("fill", 6.0)]})
    assert [f.quantity for f in runner.fills] == [4.0, 6.0]
    state = runner.order_state("s-0")
    assert state is not None and state.status is OrderStatus.FILLED
    assert runner.order_rejections == []
    accounted(runner)
    assert runner.ctx.position(X) == 10.0


def test_venue_reject_after_a_partial_releases_only_the_remainder() -> None:
    runner, _ = run(10.0, {2: ["accept"], 3: [("fill", 4.0)], 4: ["reject"]})
    assert [f.quantity for f in runner.fills] == [4.0]
    (rej,) = runner.order_rejections
    assert (rej.reason, rej.cancelled, rej.intent.quantity) == ("venue_rms", False, 6.0)
    assert runner.released_quantity(X) == 6.0
    assert runner.order_state("s-0").status is OrderStatus.REJECTED  # type: ignore[union-attr]
    accounted(runner)


def test_cancel_race_fill_and_cancel_at_the_same_ts() -> None:
    # The venue fills 4 and then answers the cancel at the same ts: queue order decides.
    runner, venue = run(10.0, {-1: [("fill", 4.0), "cancel"]}, cancel_after=2)
    assert venue.cancel_at == [2]
    assert [f.quantity for f in runner.fills] == [4.0]
    (rej,) = runner.order_rejections
    assert rej.cancelled is True and rej.intent.quantity == 6.0 and rej.ts == 2
    state = runner.order_state("s-0")
    assert state is not None and state.status is OrderStatus.CANCELLED and state.filled_qty == 4.0
    accounted(runner)


def test_cancel_race_the_completing_fill_wins() -> None:
    runner, _ = run(10.0, {-1: [("fill", 10.0), "cancel"]}, cancel_after=2)
    assert [f.quantity for f in runner.fills] == [10.0]
    assert (
        runner.order_rejections == []
    )  # the late cancel is illegal after Filled: nothing released
    assert runner.released_quantity(X) == 0.0
    assert runner.order_state("s-0").status is OrderStatus.FILLED  # type: ignore[union-attr]
    assert not runner.ctx.busy(X)


def test_expiry_releases_the_unfilled_remainder() -> None:
    runner, _ = run(10.0, {2: ["accept"], 3: [("fill", 3.0)], 5: ["expire"]})
    (rej,) = runner.order_rejections
    assert (rej.reason, rej.intent.quantity) == ("expired", 7.0)
    assert runner.order_state("s-0").status is OrderStatus.EXPIRED  # type: ignore[union-attr]
    accounted(runner)


def test_a_replayed_terminal_does_not_release_twice() -> None:
    runner, _ = run(10.0, {3: ["cancel", "cancel"]})
    assert len(runner.order_rejections) == 1
    assert runner.released_quantity(X) == 10.0
    accounted(runner)
