"""Cross-language vectors for the execution reject/cancel path (ADR 008, decision 11).

Reads ``schema/conformance/order_rejections.json``; the Rust test
``crates/honba-strategy/tests/order_rejections.rs`` reads the same file. The scenarios run
through the real ``StrategyRunner`` and ``LedgerContext`` against a scripted venue.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from honba.backtest.simulated import NextOpenExecution
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.execution import BaseExecutionPort, OrderRejection
from honba.strategies.runner import StrategyRunner

DOC = json.loads(
    (
        Path(__file__).resolve().parents[3] / "schema" / "conformance" / "order_rejections.json"
    ).read_text(encoding="utf-8")
)
PRICE = 10.0


def _iid(symbol: str) -> InstrumentId:
    return InstrumentId(symbol, "NSE")


class Scripted(Strategy):
    """Submits the scenario's intents on the first bar event of each ts."""

    name = DOC["strategy_name"]

    def __init__(self, submit: dict[str, list[list[Any]]]) -> None:
        self.submit = submit
        self.done: set[int] = set()

    def on_bar(self, bar: Bar) -> None:
        if bar.ts in self.done:
            return
        self.done.add(bar.ts)
        for side, symbol, qty in self.submit.get(str(bar.ts), []):
            make = OrderIntent.market_buy if side == "buy" else OrderIntent.market_sell
            self.ctx.submit(make(_iid(symbol), qty))


class Venue(BaseExecutionPort):
    """Fills at ``PRICE``; per order id the script says reject, partial or hold."""

    def __init__(self, script: dict[str, dict[str, Any]]) -> None:
        self.script = script
        self.working: dict[str, tuple[OrderIntent, int]] = {}
        self.fills: list[Trade] = []
        self.rejections: list[OrderRejection] = []
        self.now = 0

    def on_event(self, event: Any, ts_init: int) -> None:
        self.now = ts_init

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        spec = self.script.get(order_id, {"action": "fill"})
        action = spec["action"]
        if action == "hold":
            self.working[order_id] = (intent, ts)
            return
        filled = {"fill": intent.quantity, "reject": 0.0, "partial": spec.get("filled")}[action]
        if filled:
            self.fills.append(Trade(intent.instrument_id, intent.side, filled, PRICE, ts, order_id))
        if filled != intent.quantity:
            rest = dataclasses.replace(intent, quantity=intent.quantity - filled)
            self.rejections.append(OrderRejection(order_id, rest, spec["reason"], ts))

    def drain_fills(self) -> list[Trade]:
        out, self.fills = self.fills, []
        return out

    def drain_rejections(self) -> list[OrderRejection]:
        out, self.rejections = self.rejections, []
        return out

    def cancel(self, order_id: str) -> None:
        held = self.working.pop(order_id, None)
        if held is not None:
            intent, _ = held
            self.rejections.append(
                OrderRejection(order_id, intent, "cancelled", self.now, cancelled=True)
            )


@pytest.mark.parametrize("scenario", DOC["scenarios"], ids=lambda s: s["name"])
def test_order_rejection_vectors(scenario: dict[str, Any]) -> None:
    _run(scenario, Venue(scenario["venue"]))


LATE_CANCEL = next(s for s in DOC["scenarios"] if s["name"].startswith("late_cancel"))


def test_next_open_execution_stamps_a_late_cancel_with_the_cancel_time() -> None:
    """The real simulator agrees with the shared late-cancel vector (decision 13 addendum)."""
    venue = NextOpenExecution(cash=Money.from_major(1000.0, Currency.INR))
    _run(LATE_CANCEL, venue)


def _run(scenario: dict[str, Any], venue: Any) -> None:
    runner = StrategyRunner(Scripted(scenario["submit"]), venue)
    runner.start()
    for _, symbol, ts in scenario["events"]:
        bar = Bar(_iid(symbol), ts, PRICE, PRICE, PRICE, PRICE, 1.0)
        venue.on_event(bar, ts)
        runner.on_event(bar, ts)
        for at, order_id in scenario["cancels"]:
            if at == ts:
                runner.cancel(order_id)
    runner.stop()

    expect = scenario["expect"]
    assert [
        [
            r.order_id,
            r.intent.instrument_id.symbol,
            "buy" if r.intent.side is OrderSide.BUY else "sell",
            r.intent.quantity,
            r.reason,
            r.ts,
            r.cancelled,
        ]
        for r in runner.order_rejections
    ] == expect["order_rejections"]
    assert [[f.order_id, f.quantity] for f in runner.fills] == expect["fills"]
    for symbol, busy in expect["busy"].items():
        assert runner.ctx.busy(_iid(symbol)) is busy
    held = {i.symbol: q for i, q in runner.ctx.positions().items()}
    assert held == expect["positions"]
