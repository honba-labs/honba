"""Shared strategy conformance fixture, Python side (ADR 008).

Runs every scenario in ``schema/conformance/strategy_contract.json`` through the
Python reference strategy, ``StrategyRunner`` and ``BarCloseFills`` and compares
intents, fills, context observations, final positions and cash with the fixture.
The Rust suite (``crates/honba-strategy/tests/conformance.rs``) reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.domain.money import Money
from honba.entities import wire
from honba.entities.instrument import Instrument, InstrumentKind
from honba.strategies.context import LedgerContext
from honba.strategies.reference import BuyAndHold, ContractProbe, SmaCrossover
from honba.strategies.runner import RunResult, StrategyRunner
from honba.strategies.testing import BarCloseFills, from_wire_messages

FIXTURE = Path(__file__).resolve().parents[3] / "schema" / "conformance" / "strategy_contract.json"
DOC = json.loads(FIXTURE.read_text())
SCENARIOS = DOC["scenarios"]


def build_strategy(name: str, params: dict[str, Any]):
    iid = wire.InstrumentId.model_validate(params["instrument_id"]).to_domain()
    if name == "contract_probe":
        return ContractProbe(iid)
    if name == "buy_and_hold":
        return BuyAndHold(iid, params["quantity"])
    if name == "sma_crossover":
        return SmaCrossover(iid, params["fast"], params["slow"], params["quantity"])
    raise ValueError(f"unknown strategy {name!r}")


def build_instrument(raw: dict[str, Any]) -> Instrument:
    return Instrument(
        wire.InstrumentId.model_validate(raw["instrument_id"]).to_domain(),
        InstrumentKind(raw["kind"]),
        lot_size=raw["lot_size"],
        tick_size=raw["tick_size"],
        currency=raw["currency"],
    )


def _money(value: Money) -> dict[str, Any]:
    return wire.Money.from_domain(value).model_dump(mode="json")


def _ts(ns: int) -> dict[str, Any]:
    return wire.UnixNanos.from_ns(ns).model_dump(mode="json")


def _observation(obs: dict[str, Any]) -> dict[str, Any]:
    """A ContractProbe observation in wire form: ``now`` as ``{iso, unix_nanos}``, ``cash``
    as integer Money (ADR 0011, E11-S2)."""
    return {**obs, "now": _ts(obs["now"]), "cash": _money(obs["cash"])}


def outcome(result: RunResult, strategy) -> dict[str, Any]:
    """The run in the fixture's JSON shape."""
    return {
        "intents": [
            {
                "ts_init": _ts(s.ts_init),
                "intent": wire.OrderIntent.from_domain(s.intent).model_dump(mode="json"),
            }
            for s in result.intents
        ],
        "fills": [
            wire.Trade(
                order_id=f.order_id,
                instrument_id=wire.InstrumentId.from_domain(f.instrument_id),
                side=f.side,
                quantity=f.quantity,
                price=f.price,
                costs=wire.Money.from_domain(f.costs),
                ts_event=wire.UnixNanos.from_ns(f.ts),
                ts_init=wire.UnixNanos.from_ns(f.ts),
            ).model_dump(mode="json")
            for f in result.fills
        ],
        "observations": [_observation(o) for o in getattr(strategy, "observations", [])],
        "positions": [
            {"instrument_id": {"symbol": i.symbol, "exchange": i.exchange}, "quantity": q}
            for i, q in result.ctx.positions().items()
        ],
        "cash": _money(result.ctx.cash()),
    }


def run_python(scenario: dict[str, Any]) -> dict[str, Any]:
    strategy = build_strategy(scenario["strategy"], scenario["params"])
    ctx = LedgerContext(
        cash=scenario["initial_cash"],
        instruments=[build_instrument(i) for i in scenario["instruments"]],
    )
    costs = scenario.get("fill_costs", {})
    execution = BarCloseFills(flat_cost=costs.get("flat", 0.0), cost_bps=costs.get("bps", 0.0))
    runner = StrategyRunner(strategy, execution, ctx=ctx)
    result = runner.run(from_wire_messages(json.dumps(scenario["events"])))
    return outcome(result, strategy)


def test_fixture_header():
    assert DOC["schema_version"] == wire.SCHEMA_VERSION
    assert DOC["type"] == "StrategyConformance"
    assert DOC["fill_model"] == "bar_close"
    names = [s["name"] for s in SCENARIOS]
    assert len(names) == len(set(names))
    assert {s["strategy"] for s in SCENARIOS} == {"contract_probe", "buy_and_hold", "sma_crossover"}


def test_fixture_covers_non_zero_costs():
    costed = [s for s in SCENARIOS if s.get("fill_costs")]
    assert costed, "no scenario exercises fill costs"
    for s in costed:
        assert any(f["costs"]["amount"] > 0 for f in s["expected"]["fills"]), s["name"]


def test_fixture_payloads_are_valid_wire_values():
    for s in SCENARIOS:
        for m in s["events"]:
            wire.Message.model_validate(m)
        for i in s["expected"]["intents"]:
            wire.OrderIntent.model_validate(i["intent"])
        for f in s["expected"]["fills"]:
            wire.Trade.model_validate(f)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_python_run_matches_the_fixture(scenario):
    got = run_python(scenario)
    want = scenario["expected"]
    for key in ("intents", "fills", "observations", "positions", "cash"):
        assert got[key] == want[key], f"{scenario['name']}: {key}"


def run_rust(scenario: dict[str, Any]) -> dict[str, Any]:
    from honba import _honba

    costs = scenario.get("fill_costs", {})
    out = _honba.run_strategy(
        scenario["strategy"],
        json.dumps(scenario["params"]),
        json.dumps(scenario["events"]),
        json.dumps(scenario["instruments"]),
        scenario["initial_cash"],
        flat_cost=costs.get("flat", 0.0),
        cost_bps=costs.get("bps", 0.0),
    )
    return json.loads(out)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_rust_strategy_through_the_binding_matches_python_and_the_fixture(scenario):
    rust, python = run_rust(scenario), run_python(scenario)
    # Rust also reports typed ``rejections``; every fixture scenario is a valid run.
    assert rust.pop("rejections") == [], scenario["name"]
    assert rust == python, scenario["name"]
    assert rust == scenario["expected"], scenario["name"]


def test_run_strategy_rejects_unknown_strategies_and_bad_json():
    from honba import _honba

    params = json.dumps({"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 1.0})
    with pytest.raises(ValueError, match="unknown strategy"):
        _honba.run_strategy("nope", params, "[]")
    with pytest.raises(ValueError):
        _honba.run_strategy("buy_and_hold", params, "not json")
    assert json.loads(_honba.run_strategy("buy_and_hold", params, "[]"))["fills"] == []


def test_run_strategy_costs_default_to_none_and_invalid_costs_raise_value_error():
    from honba import _honba

    params = json.dumps({"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 1.0})
    for kwargs in (
        {"flat_cost": -1.0},
        {"flat_cost": float("nan")},
        {"flat_cost": 1e12},
        {"cost_bps": -1.0},
        {"cost_bps": float("inf")},
        {"cost_bps": 10_001.0},
    ):
        with pytest.raises(ValueError, match="fill cost"):
            _honba.run_strategy("buy_and_hold", params, "[]", **kwargs)
