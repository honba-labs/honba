"""The reject/cancel path of the ``ExecutionPort`` contract (ADR 008).

A port reports an order (or the part of one) that will never fill as an
``OrderRejection`` from ``drain_rejections``; the runner releases it from the context,
so ports no longer need a handle on ``ctx.release``. Ports that predate the path keep
working through the ``drain_port_rejections`` / ``cancel_order`` shims.
"""

from __future__ import annotations

import dataclasses
import warnings

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.execution import (
    BaseExecutionPort,
    ExecutionPort,
    OrderRejection,
    RejectingExecutionPort,
    cancel_order,
    drain_port_rejections,
)
from honba.strategies.runner import StrategyRunner

X = InstrumentId("X", "NSE")


def bar(ts: int) -> Bar:
    return Bar(X, ts, 10.0, 10.0, 10.0, 10.0, 1.0)


class Scripted(Strategy):
    name = "s"

    def __init__(self, intents: list[OrderIntent]) -> None:
        self.intents = intents

    def on_bar(self, bar: Bar) -> None:
        for intent in self.intents:
            self.ctx.submit(intent)
        self.intents = []


class HoldingPort(BaseExecutionPort):
    """Holds orders until told to fill, reject or cancel them."""

    def __init__(self) -> None:
        self.orders: dict[str, OrderIntent] = {}
        self.fills: list[Trade] = []
        self.rejections: list[OrderRejection] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self.orders[order_id] = intent

    def drain_fills(self) -> list[Trade]:
        out, self.fills = self.fills, []
        return out

    def drain_rejections(self) -> list[OrderRejection]:
        out, self.rejections = self.rejections, []
        return out

    def cancel(self, order_id: str) -> None:
        intent = self.orders.pop(order_id, None)
        if intent is not None:
            self.rejections.append(OrderRejection(order_id, intent, "cancelled", cancelled=True))

    def partial(self, order_id: str, filled: float) -> None:
        intent = self.orders.pop(order_id)
        self.fills.append(Trade(X, intent.side, filled, 10.0, 1, order_id))
        rest = dataclasses.replace(intent, quantity=intent.quantity - filled)
        self.rejections.append(OrderRejection(order_id, rest, "insufficient funds"))


class LegacyPort:
    """A port written before the reject/cancel path: only submit and drain_fills."""

    def __init__(self) -> None:
        self.orders: list[str] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self.orders.append(order_id)

    def drain_fills(self) -> list[Trade]:
        return []


def test_a_port_rejection_releases_the_intent_in_the_context() -> None:
    port = HoldingPort()
    runner = StrategyRunner(Scripted([OrderIntent.market_buy(X, 10)]), port)
    runner.start()
    runner.on_event(bar(1), 1)
    assert runner.ctx.busy(X)
    port.partial("s-0", filled=4)
    runner.on_event(bar(2), 2)
    assert runner.ctx.position(X) == 4.0
    assert not runner.ctx.busy(X)  # 4 filled + 6 rejected: nothing left pending
    assert [(r.order_id, r.intent.quantity, r.reason) for r in runner.order_rejections] == [
        ("s-0", 6, "insufficient funds")
    ]


def test_cancel_goes_through_the_port_and_is_booked_at_once() -> None:
    port = HoldingPort()
    runner = StrategyRunner(Scripted([OrderIntent.market_sell(X, 3)]), port)
    runner.start()
    runner.on_event(bar(1), 1)
    assert runner.cancel("s-0") is True
    assert not runner.ctx.busy(X)
    (rejection,) = runner.order_rejections
    assert rejection.cancelled and rejection.order_id == "s-0"
    assert rejection.intent.side is OrderSide.SELL


def test_rejections_appear_in_the_run_result() -> None:
    class RejectAll(HoldingPort):
        def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
            self.rejections.append(OrderRejection(order_id, intent, "market closed", ts))

    result = StrategyRunner(Scripted([OrderIntent.market_buy(X, 1)]), RejectAll()).run(
        [(bar(1), 1)]
    )
    assert [(r.order_id, r.ts, r.cancelled) for r in result.order_rejections] == [("s-0", 1, False)]
    assert not result.ctx.busy(X)


def test_a_legacy_port_keeps_working_and_cannot_cancel() -> None:
    port = LegacyPort()
    runner = StrategyRunner(Scripted([OrderIntent.market_buy(X, 1)]), port)
    result = runner.run([(bar(1), 1)])
    assert port.orders == ["s-0"]
    assert result.order_rejections == []
    assert runner.cancel("s-0") is False
    assert runner.ctx.busy(X)  # a legacy port never reports anything back


def test_shims_tolerate_ports_without_the_optional_methods() -> None:
    assert drain_port_rejections(LegacyPort()) == []
    assert cancel_order(LegacyPort(), "x") is False
    port = HoldingPort()
    port.submit("a", OrderIntent.market_buy(X, 1), 0)
    assert cancel_order(port, "a") is True
    assert [r.order_id for r in drain_port_rejections(port)] == ["a"]


def test_protocols_classify_ports() -> None:
    assert isinstance(HoldingPort(), RejectingExecutionPort)
    assert isinstance(LegacyPort(), ExecutionPort)
    assert not isinstance(LegacyPort(), RejectingExecutionPort)


def test_base_port_defaults_are_inert() -> None:
    class Minimal(BaseExecutionPort):
        def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

        def drain_fills(self) -> list[Trade]:
            return []

    port = Minimal()
    port.cancel("unknown")  # default: nothing to cancel
    assert port.drain_rejections() == []
    with pytest.raises(TypeError):
        BaseExecutionPort()  # type: ignore[abstract]


def test_a_handle_rejected_override_sees_port_rejections() -> None:
    seen: list[OrderIntent] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class Handles(Scripted):
            name = "h"

            def handle_rejected(self, intent: OrderIntent) -> None:
                seen.append(intent)
                super().handle_rejected(intent)

    port = HoldingPort()
    runner = StrategyRunner(Handles([OrderIntent.market_buy(X, 2)]), port)
    runner.start()
    runner.on_event(bar(1), 1)
    runner.cancel("h-0")
    assert [i.quantity for i in seen] == [2]
    assert not runner.ctx.busy(X)


def test_order_rejection_requires_an_order_id() -> None:
    with pytest.raises(ValueError):
        OrderRejection("", OrderIntent.market_buy(X, 1), "x")


def test_cancelled_and_reason_cannot_disagree() -> None:
    intent = OrderIntent.market_buy(X, 1)
    ok = OrderRejection("o", intent, "cancelled", cancelled=True)
    assert ok.cancelled
    assert not OrderRejection("o", intent, "no_position").cancelled
    with pytest.raises(ValueError, match="cancelled"):
        OrderRejection("o", intent, "cancelled")  # a cancel must say so
    with pytest.raises(ValueError, match="cancelled"):
        OrderRejection("o", intent, "no_position", cancelled=True)
