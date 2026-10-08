"""Risk stage golden vectors, Python side (ADR 0018 decision 11).

Replays every case of ``schema/conformance/risk_decisions.json`` through
``honba._honba.RiskStage`` (one fresh stage per case; the rate rule has memory) and compares
the decision, the wire code and the whole ``context`` with the fixture: integers exactly, floats
within 1e-9, key sets exactly. The Rust runner
(``crates/honba-risk/tests/conformance.rs``) reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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
