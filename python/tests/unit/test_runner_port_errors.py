"""A failing port or ``on_fill`` hook must not strand drained intents or fills (ADR 008).

An exception out of ``on_event`` is terminal for the run, but the context stays
consistent: intents that were drained and never sent are released, and every fill the
port handed over is booked.
"""

from __future__ import annotations

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.execution import BaseExecutionPort
from honba.strategies.runner import StrategyRunner

A, B, C = InstrumentId("A", "NSE"), InstrumentId("B", "NSE"), InstrumentId("C", "NSE")


def bar(ts: int) -> Bar:
    return Bar(A, ts, 10.0, 10.0, 10.0, 10.0, 1.0)


class ThreeBuys(Strategy):
    name = "three"

    def __init__(self, fail_on_fill: bool = False) -> None:
        self.sent = False
        self.fail_on_fill = fail_on_fill

    def on_bar(self, bar: Bar) -> None:
        if not self.sent:
            self.sent = True
            for iid in (A, B, C):
                self.ctx.submit(OrderIntent.market_buy(iid, 1))

    def on_fill(self, fill: Trade) -> None:
        if self.fail_on_fill:
            raise RuntimeError("on_fill failed")


class FlakyPort(BaseExecutionPort):
    def __init__(self, fail_at: int | None = None, fill_all: bool = False) -> None:
        self.fail_at = fail_at
        self.fill_all = fill_all
        self.submits = 0

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        n = self.submits
        self.submits += 1
        if n == self.fail_at:
            raise ConnectionError("port down")

    def drain_fills(self) -> list[Trade]:
        if not self.fill_all:
            return []
        self.fill_all = False
        return [
            Trade(iid, OrderSide.BUY, 1.0, 10.0, 1, f"three-{i}") for i, iid in enumerate((A, B, C))
        ]


def test_a_submit_error_releases_the_failed_and_remaining_intents() -> None:
    runner = StrategyRunner(ThreeBuys(), FlakyPort(fail_at=1))
    runner.start()
    with pytest.raises(ConnectionError):
        runner.on_event(bar(1), 1)
    assert [i.order_id for i in runner.intents] == ["three-0"]
    assert runner.ctx.busy(A)
    assert not runner.ctx.busy(B)
    assert not runner.ctx.busy(C)


def test_a_failing_on_fill_does_not_drop_the_remaining_fills() -> None:
    runner = StrategyRunner(ThreeBuys(fail_on_fill=True), FlakyPort(fill_all=True))
    runner.start()
    with pytest.raises(RuntimeError, match="on_fill failed"):
        runner.on_event(bar(1), 1)
    assert len(runner.fills) == 3
    for iid in (A, B, C):
        assert runner.ctx.position(iid) == 1.0
        assert not runner.ctx.busy(iid)
