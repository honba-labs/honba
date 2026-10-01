"""Cross-language wire-contract round trip (E0-S2 acceptance, ADR 006).

For every golden vector: Rust -> JSON -> Python -> JSON -> Rust must be
lossless, and Rust and Python must agree on which payloads are invalid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from honba.entities import wire
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent

_honba = pytest.importorskip("honba._honba")

GOLDEN = Path(__file__).resolve().parents[3] / "schema" / "golden"


def _cases(key: str):
    for path in sorted(GOLDEN.glob("*.json")):
        doc = json.loads(path.read_text())
        for case in doc.get(key, []):
            yield pytest.param(doc["type"], case["value"], id=f"{path.name}:{case['name']}")


def test_schema_version_agrees():
    assert _honba.SCHEMA_VERSION == wire.SCHEMA_VERSION


@pytest.mark.parametrize(("kind", "golden"), list(_cases("cases")))
def test_rust_python_rust_roundtrip_is_lossless(kind, golden):
    adapter = TypeAdapter(wire.MODELS[kind])
    # Rust -> JSON
    rust_json = _honba.canonical_json(kind, json.dumps(golden))
    assert json.loads(rust_json) == golden
    # JSON -> Python -> JSON
    model = adapter.validate_json(rust_json)
    py_json = adapter.dump_json(model).decode()
    assert json.loads(py_json) == golden
    # JSON -> Rust (and back out again, byte-identical to the first pass)
    assert _honba.canonical_json(kind, py_json) == rust_json


@pytest.mark.parametrize(("kind", "bad"), list(_cases("invalid")))
def test_rust_and_python_reject_the_same_payloads(kind, bad):
    payload = json.dumps(bad)
    with pytest.raises(ValueError):
        _honba.canonical_json(kind, payload)
    with pytest.raises(ValidationError):
        TypeAdapter(wire.MODELS[kind]).validate_json(payload)


def test_strategy_intent_survives_rust_roundtrip():
    nifty = InstrumentId("NIFTY50", "NSE")
    intents = [
        OrderIntent.market_buy(nifty, 75),
        OrderIntent.limit_sell(nifty, 25, 22100.5),
        OrderIntent.stop_buy(nifty, 75, 22050.0),
        OrderIntent.stop_limit_sell(nifty, 75, 21950.0, 21940.0),
    ]
    for intent in intents:
        payload = wire.OrderIntent.from_domain(intent).model_dump_json()
        back = wire.OrderIntent.model_validate_json(_honba.canonical_json("OrderIntent", payload))
        assert back.to_domain() == intent
