"""Risk stage golden vectors, Python side (ADR 0018 decision 11).

Replays every case of ``schema/conformance/risk_decisions.json`` through
``honba._honba.RiskStage`` (one fresh stage per case; the rate rule has memory) and compares
the decision, the wire code and the whole ``context`` with the fixture: integers exactly, floats
within 1e-9, key sets exactly. The Rust runner
(``crates/honba-risk/tests/conformance.rs``) reads the same file.

The same cases also run through the Python ``StrategyRunner`` with a scripted execution port
(ADR 0018 decision 11): per request the runner is fed an event, the strategy submits the
request's order, and the port must see a submit exactly when the fixture says ``approved``;
a refusal must leave the order ``Rejected`` in the runner with the case's code, reason and
``context`` (read back from the runner's audit). The request's ``position`` is delivered as
ledger position plus the runner's own working exposure; ``reference_price`` as a trade tick.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderStatus, OrderType
from honba.entities.tick import AggressorSide, QuoteTick, TradeTick
from honba.risk import RiskRefused
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner

_honba = pytest.importorskip("honba._honba")

FIXTURE = Path(__file__).resolve().parents[3] / "schema" / "conformance" / "risk_decisions.json"
DOC = json.loads(FIXTURE.read_text())
EPS = 1e-9


def _instrument(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    symbol, exchange = name.rsplit(".", 1)
    return {
        "instrument_id": {"symbol": symbol, "exchange": exchange},
        "kind": "equity",
        "currency": DOC["currency"],
        **spec,
    }


def _same(expected: Any, actual: Any) -> bool:
    """Integers exact, floats within ``EPS``, objects with identical key sets."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, float) or isinstance(actual, float):
        return (
            isinstance(expected, float)
            and isinstance(actual, float)
            and abs(expected - actual) <= EPS
        )
    if isinstance(expected, int) and isinstance(actual, int):
        return expected == actual
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _same(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_same(x, y) for x, y in zip(expected, actual))
    return bool(expected == actual)


def _observed(decision: Any) -> dict[str, Any]:
    if decision.approved:
        return {"decision": "approved"}
    return {"decision": "refused", "code": decision.code, "context": decision.context}


def test_fixture_is_the_expected_document() -> None:
    assert DOC["fixture_version"] == 1
    assert DOC["type"] == "RiskDecision"
    assert len(DOC["cases"]) >= 79


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_risk_conformance(case: dict[str, Any]) -> None:
    instruments = [_instrument(n, s) for n, s in DOC["instruments"].items()]
    stage = _honba.RiskStage(
        _honba.RiskLimits.from_dict(case["limits"]), DOC["currency"], "null", instruments
    )
    assert len(case["requests"]) == len(case["expect"])
    for i, (request, expect) in enumerate(zip(case["requests"], case["expect"])):
        got = _observed(stage.check(request))
        assert _same(expect, got), f"{case['name']}[{i}]\n expected {expect}\n actual   {got}"


def test_decision_attributes_agree_with_the_context() -> None:
    instruments = [_instrument(n, s) for n, s in DOC["instruments"].items()]
    case = next(c for c in DOC["cases"] if c["expect"][-1].get("code") == "risk_trading_halted")
    stage = _honba.RiskStage(
        _honba.RiskLimits.from_dict(case["limits"]), DOC["currency"], "null", instruments
    )
    decision = None
    for request in case["requests"]:
        decision = stage.check(request)
    assert decision is not None and not decision.approved
    assert decision.code == "risk_trading_halted"
    assert decision.rule == decision.context["rule"] == "trading_halted"

    approved = _honba.RiskStage(_honba.RiskLimits(), DOC["currency"], "null", instruments).check(
        DOC["cases"][0]["requests"][0]
    )
    assert approved.approved
    assert (approved.code, approved.rule, approved.context) == (None, None, {})


# --- the same cases through the Python StrategyRunner with a scripted port ---------------------


def _instrument_id(name: str) -> InstrumentId:
    symbol, exchange = name.rsplit(".", 1)
    return InstrumentId(symbol, exchange)


def _intent(request: dict[str, Any]) -> OrderIntent:
    price, trigger = request.get("price"), request.get("trigger_price")
    kind = {
        (False, False): OrderType.MARKET,
        (True, False): OrderType.LIMIT,
        (False, True): OrderType.STOP_MARKET,
        (True, True): OrderType.STOP_LIMIT,
    }[(price is not None, trigger is not None)]
    return OrderIntent(
        _instrument_id(request["instrument_id"]),
        OrderSide.BUY if request["side"] == "buy" else OrderSide.SELL,
        request["quantity"],
        kind,
        price,
        trigger_price=trigger,
    )


class _ScriptedPort:
    """Records every submit; keeps each order working (no fills, no acknowledgements)."""

    def __init__(self) -> None:
        self.submits: list[tuple[str, OrderIntent, int]] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self.submits.append((order_id, intent, ts))

    def drain_fills(self) -> list[Any]:
        return []


class _Driver(Strategy):
    """Submits whatever was queued, on the next event of any kind."""

    name = "r"

    def __init__(self) -> None:
        self.next: OrderIntent | None = None

    def _go(self) -> None:
        if self.next is not None:
            self.ctx.submit(self.next)
            self.next = None

    def on_bar(self, bar: Any) -> None:
        self._go()

    def on_quote(self, quote: QuoteTick) -> None:
        self._go()

    def on_trade(self, trade: TradeTick) -> None:
        self._go()


class _SeededContext(LedgerContext):
    """Reports the request's ``position`` once the runner's working exposure is added."""

    def __init__(self) -> None:
        super().__init__()
        self.target = 0.0
        self.side = OrderSide.BUY
        self.runner: StrategyRunner | None = None

    def position(self, instrument_id: InstrumentId) -> float:
        assert self.runner is not None
        return self.target - self.runner.working_exposure(instrument_id, self.side)


def _event(request: dict[str, Any]) -> TradeTick | QuoteTick:
    iid, ts = _instrument_id(request["instrument_id"]), request["ts"]
    if "reference_price" in request:  # a trade is the only event here that sets the reference
        return TradeTick(iid, ts, request["reference_price"], 1.0, AggressorSide.BUYER, "t")
    return QuoteTick(iid, ts, 99.0, 101.0, 1.0, 1.0)


def _runner_for(case: dict[str, Any]) -> tuple[StrategyRunner, _Driver, _ScriptedPort, Any]:
    instruments = [_instrument(n, s) for n, s in DOC["instruments"].items()]
    stage = _honba.RiskStage(
        _honba.RiskLimits.from_dict(case["limits"]), DOC["currency"], "null", instruments
    )
    ctx, strategy, port = _SeededContext(), _Driver(), _ScriptedPort()
    runner = StrategyRunner(strategy, port, ctx, risk=stage)
    ctx.runner = runner
    return runner, strategy, port, _honba.TradingState


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_risk_conformance_through_the_python_runner(case: dict[str, Any]) -> None:
    runner, strategy, port, states = _runner_for(case)
    ctx = runner.ctx
    assert isinstance(ctx, _SeededContext)
    for i, (request, expect) in enumerate(zip(case["requests"], case["expect"])):
        where = f"{case['name']}[{i}]"
        try:
            strategy.next = _intent(request)
        except ValueError:
            # Python refuses the intent at construction (quantity <= 0): it never reaches the gate.
            assert expect["decision"] == "refused", where
            continue
        ctx.target, ctx.side = request["position"], strategy.next.side
        runner.set_trading_state(getattr(states, request["trading_state"].upper()))
        before = len(port.submits)
        runner.on_event(_event(request), request["ts"])
        order_id = f"r-{i}"
        if expect["decision"] == "approved":
            assert [o[0] for o in port.submits[before:]] == [order_id], where
            assert port.submits[-1][2] == request["ts"], where
            continue
        assert len(port.submits) == before, f"{where}: a refused order reached the port"
        state = runner.order_state(order_id)
        assert state is not None and state.status is OrderStatus.REJECTED, where
        rejection = runner.order_rejections[-1]
        assert (rejection.order_id, rejection.reason) == (order_id, expect["code"]), where
        refused = runner.audit[-2]
        assert isinstance(refused, RiskRefused), where
        got = {"decision": "refused", "code": refused.code, "context": refused.context}
        assert _same(expect, got), f"{where}\n expected {expect}\n actual   {got}"
