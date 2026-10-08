"""The Python ``StrategyRunner`` risk gate (ADR 0018 decisions 6, 7, 10).

Mirrors ``StrategyRunner::with_risk`` in ``crates/honba-strategy/src/runner.rs``: the stage runs
before the port's ``submit``; a refusal never reaches the port, consumes an order id, is audited
(``RiskRefused`` then ``OrderRejected``) and travels the one event stream as a ``Rejected`` event
whose reason is the ``ErrorCode`` wire spelling. Cross-language numbers live in
``tests/integration/test_risk_conformance.py``.
"""

from __future__ import annotations

import gc

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderStatus
from honba.entities.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.risk import OrderRejected as AuditOrderRejected
from honba.risk import RiskLimits, RiskRefused, RiskStage, TradingState
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner

pytest.importorskip("honba._honba")

X = InstrumentId("X", "NSE")
INSTRUMENT = {
    "instrument_id": {"symbol": "X", "exchange": "NSE"},
    "kind": "equity",
    "currency": "INR",
    "lot_size": 1.0,
    "tick_size": 0.05,
}


def stage(**limits: object) -> RiskStage:
    return RiskStage(RiskLimits(**limits), "INR", "null", [INSTRUMENT])  # type: ignore[arg-type]


def bar(ts: int, close: float = 100.0) -> Bar:
    return Bar(X, ts, close, close, close, close, 1.0)


class Script(Strategy):
    """Submits the next scripted intent(s) on every bar."""

    name = "s"

    def __init__(self, *batches: list[OrderIntent]) -> None:
        self.batches = list(batches)

    def on_bar(self, bar: Bar) -> None:
        for intent in self.batches.pop(0) if self.batches else []:
            self.ctx.submit(intent)


class Port:
    """Records submits and keeps every order working (no fills)."""

    def __init__(self) -> None:
        self.orders: list[tuple[str, OrderIntent, int]] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self.orders.append((order_id, intent, ts))

    def drain_fills(self) -> list[Trade]:
        return []


def buy(qty: float, price: float | None = None) -> OrderIntent:
    if price is None:
        return OrderIntent.market_buy(X, qty)
    return OrderIntent.limit_buy(X, qty, price)


def sell(qty: float) -> OrderIntent:
    return OrderIntent.market_sell(X, qty)


def held(ctx: LedgerContext, qty: float) -> None:
    ctx.apply_fill(Trade(X, OrderSide.BUY, qty, 1.0, 1, "seed"))


def test_a_refusal_never_reaches_the_port_and_is_rejected_with_the_error_code() -> None:
    port = Port()
    runner = StrategyRunner(Script([buy(10, 100.0)]), port, risk=stage(max_notional=500.0))
    result = runner.run([(bar(1), 1)])
    assert port.orders == []
    assert result.intents == []
    assert [(r.order_id, r.reason, r.cancelled) for r in result.order_rejections] == [
        ("s-0", "risk_max_notional_exceeded", False)
    ]
    state = runner.order_state("s-0")
    assert state is not None and state.status is OrderStatus.REJECTED
    assert not result.ctx.busy(X)  # released like a venue reject


def test_an_approved_order_reaches_the_port_with_its_ts_init() -> None:
    port = Port()
    runner = StrategyRunner(Script([buy(10, 100.0)]), port, risk=stage(max_notional=5000.0))
    result = runner.run([(bar(7), 7)])
    assert [(o[0], o[2]) for o in port.orders] == [("s-0", 7)]
    assert result.order_rejections == []
    assert runner.audit == ()


def test_a_refused_order_consumes_its_order_id() -> None:
    port = Port()
    runner = StrategyRunner(
        Script([buy(100, 100.0)], [buy(1, 100.0)]), port, risk=stage(max_notional=500.0)
    )
    runner.run([(bar(1), 1), (bar(2), 2)])
    assert [o[0] for o in port.orders] == ["s-1"]


def test_audit_records_risk_refused_then_order_rejected_per_refusal() -> None:
    runner = StrategyRunner(Script([buy(100, 100.0)]), Port(), risk=stage(max_notional=500.0))
    runner.run([(bar(5), 5)])
    refused, rejected = runner.audit
    assert isinstance(refused, RiskRefused) and isinstance(rejected, AuditOrderRejected)
    assert (refused.order_id, refused.code, refused.rule) == (
        "s-0",
        "risk_max_notional_exceeded",
        "max_notional",
    )
    assert refused.context["rule"] == "max_notional"
    assert (rejected.order_id, rejected.reason) == ("s-0", "risk_max_notional_exceeded")
    assert refused.ts == rejected.ts == 5


def test_reference_price_is_the_last_close_seen_before_the_strategy_acts() -> None:
    # a market order is priced by the reference: 10 x 100.0 = 1000 > 500
    runner = StrategyRunner(Script([buy(10)]), Port(), risk=stage(max_notional=500.0))
    runner.run([(bar(1, 100.0), 1)])
    (refused, _) = runner.audit
    assert refused.code == "risk_max_notional_exceeded"
    assert refused.context["notional"] == pytest.approx(1000.0)


def test_trade_price_is_a_reference_and_quotes_are_not() -> None:
    def tick(ts: int, price: float) -> TradeTick:
        return TradeTick(X, ts, price, 1.0, AggressorSide.BUYER, "t")

    class OnTrade(Script):
        def on_trade(self, trade: TradeTick) -> None:
            self.on_bar(None)  # type: ignore[arg-type]

        def on_quote(self, quote: QuoteTick) -> None:
            self.on_bar(None)  # type: ignore[arg-type]

    quote = QuoteTick(X, 1, 99.0, 101.0, 1.0, 1.0)
    runner = StrategyRunner(OnTrade([buy(10)], [buy(10)]), Port(), risk=stage(max_notional=500.0))
    runner.on_event(quote, 1)  # no price known: unpriceable
    runner.on_event(tick(2, 100.0), 2)  # priced by the trade
    first, _, second, _ = runner.audit
    assert first.context["reason"] == "unpriceable"
    assert second.context["notional"] == pytest.approx(1000.0)


def test_position_is_the_ledger_plus_the_runner_working_exposure() -> None:
    # ADR 0019 example: long 100, a working sell 60, a new sell 50 breaches reduce-only
    ctx = LedgerContext()
    held(ctx, 100.0)
    port = Port()
    runner = StrategyRunner(Script([sell(60)], [sell(50)]), port, ctx, risk=stage())
    runner.set_trading_state(TradingState.REDUCING)
    runner.run([(bar(1), 1), (bar(2), 2)])
    assert [o[0] for o in port.orders] == ["s-0"]
    (refused, _) = runner.audit
    assert refused.code == "risk_reduce_only_violation"
    assert refused.context["position"] == pytest.approx(40.0)  # 100 - 60 working


def test_halted_and_reduce_only_hold_without_a_stage() -> None:
    ctx = LedgerContext()
    held(ctx, 10.0)
    port = Port()
    runner = StrategyRunner(Script([buy(1)], [sell(5)], [buy(1)]), port, ctx)
    assert runner.trading_state == TradingState.ACTIVE
    runner.set_trading_state(TradingState.HALTED)
    runner.on_event(bar(1), 1)
    runner.set_trading_state(TradingState.REDUCING)
    runner.on_event(bar(2), 2)
    runner.on_event(bar(3), 3)
    assert [o[1].side for o in port.orders] == [OrderSide.SELL]
    assert [r.reason for r in runner.order_rejections] == [
        "risk_trading_halted",
        "risk_reduce_only_violation",
    ]
    assert not runner.holds_risk_stage


def test_trading_state_must_be_a_trading_state() -> None:
    runner = StrategyRunner(Script(), Port())
    with pytest.raises(TypeError):
        runner.set_trading_state("halted")  # type: ignore[arg-type]


def test_rate_window_runs_on_ts_init_nanoseconds() -> None:
    runner = StrategyRunner(
        Script([buy(1)], [buy(1)], [buy(1)]), Port(), risk=stage(order_rate=(1, 1000))
    )
    for ts in (0, 999_000_000, 1_000_000_000):
        runner.on_event(bar(ts), ts)
    assert [r.reason for r in runner.order_rejections] == ["risk_order_rate_exceeded"]
    assert runner.audit[0].ts == 999_000_000


def test_a_stage_serves_one_runner() -> None:
    shared = stage()
    first = StrategyRunner(Script(), Port(), risk=shared)
    assert first.holds_risk_stage
    with pytest.raises(ValueError, match="one risk stage"):
        StrategyRunner(Script(), Port(), risk=shared)
    del first
    gc.collect()
    StrategyRunner(Script(), Port(), risk=shared)  # the first runner is gone


def test_a_port_that_gates_itself_cannot_sit_behind_a_runner_stage() -> None:
    class Gated(Port):
        holds_risk_stage = True

    with pytest.raises(ValueError, match="one risk stage"):
        StrategyRunner(Script(), Gated(), risk=stage())
    StrategyRunner(Script(), Gated())  # no runner stage: nothing doubled


def test_require_live_limits_needs_both_limits() -> None:
    both = RiskLimits(max_notional=1.0, order_rate=(1, 1000))
    ok = StrategyRunner(Script(), Port(), risk=stage(), risk_limits=both)
    ok.require_live_limits()
    for runner in (
        StrategyRunner(Script(), Port()),
        StrategyRunner(Script(), Port(), risk=stage()),
        StrategyRunner(Script(), Port(), risk=stage(), risk_limits=RiskLimits(max_notional=1.0)),
    ):
        with pytest.raises(ValueError):
            runner.require_live_limits()
