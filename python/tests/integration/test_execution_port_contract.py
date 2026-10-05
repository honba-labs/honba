"""Shared ``ExecutionPort`` contract, run against every port the core ships (ADR 008).

Each port is driven by the real ``StrategyRunner`` over the same bar stream and must:

* attribute every fill to an order the runner submitted;
* never fill or release more than an order's quantity;
* account for every order once the run ends and its working orders are cancelled:
  ``filled + released == ordered``, so the context is left with nothing busy;
* return each fill and rejection exactly once (drains empty the queues);
* treat cancelling an unknown order as a no-op.

Add every new core port to ``PORTS``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.execution import ExecutionPort, cancel_order, drain_port_rejections
from honba.strategies.runner import StrategyRunner
from honba.strategies.testing import BarCloseFills

A = InstrumentId("AAA", "NSE")
B = InstrumentId("BBB", "NSE")

PORTS: dict[str, Callable[[], ExecutionPort]] = {
    "bar_close_fills": BarCloseFills,
}


def _bars() -> list[tuple[Bar, int]]:
    out = []
    for day in range(1, 7):
        ts = day * 86_400 * 10**9
        for iid, px in ((A, 100.0 + day), (B, 50.0 + day)):
            out.append((Bar(iid, ts, px, px + 1, px - 1, px + 0.5, 1_000.0), ts))
    return out


class Churn(Strategy):
    """Buys both instruments, sells them, buys again; the last order is working at the end."""

    name = "churn"

    def __init__(self) -> None:
        self.n = 0

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id != B:
            return  # one decision per session, after both bars
        self.n += 1
        if self.n == 1:
            self.ctx.submit(OrderIntent.market_buy(A, 10))
            self.ctx.submit(OrderIntent.market_buy(B, 5))
        elif self.n == 3:
            self.ctx.submit(OrderIntent.market_sell(A, 10))
            self.ctx.submit(OrderIntent.market_sell(B, 5))
        elif self.n == 5:
            self.ctx.submit(OrderIntent.market_buy(A, 3))
        elif self.n == 6:
            self.ctx.submit(OrderIntent.market_buy(B, 2))  # still working at the end


@pytest.fixture(params=sorted(PORTS))
def port(request: pytest.FixtureRequest) -> ExecutionPort:
    return PORTS[request.param]()


def _run(port: ExecutionPort) -> StrategyRunner:
    runner = StrategyRunner(Churn(), port, ctx=LedgerContext(cash=1_000_000.0))
    runner.run(_bars())
    for submitted in runner.intents:
        runner.cancel(submitted.order_id)
    return runner


def test_every_fill_belongs_to_a_submitted_order(port: ExecutionPort) -> None:
    runner = _run(port)
    ids = {s.order_id for s in runner.intents}
    assert runner.fills
    assert {f.order_id for f in runner.fills} <= ids
    assert {r.order_id for r in runner.order_rejections} <= ids


def test_orders_are_fully_accounted_for(port: ExecutionPort) -> None:
    runner = _run(port)
    done: dict[str, float] = defaultdict(float)
    for fill in runner.fills:
        done[fill.order_id] += fill.quantity
    for rejection in runner.order_rejections:
        done[rejection.order_id] += rejection.intent.quantity
    for submitted in runner.intents:
        assert done[submitted.order_id] == pytest.approx(submitted.intent.quantity), submitted
    assert not runner.ctx.busy(A) and not runner.ctx.busy(B)


def test_drains_return_each_report_once(port: ExecutionPort) -> None:
    _run(port)
    assert port.drain_fills() == []
    assert drain_port_rejections(port) == []


def test_cancelling_an_unknown_order_is_a_no_op(port: ExecutionPort) -> None:
    runner = _run(port)
    before = list(runner.order_rejections)
    cancel_order(port, "no-such-order")
    assert drain_port_rejections(port) == []
    assert runner.order_rejections == before
