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
    with pytest.raises(ValueError):
        wire.loads(kind, payload)


def _tolerated_cases():
    for path in sorted(GOLDEN.glob("*.json")):
        doc = json.loads(path.read_text())
        for case in doc.get("tolerated", []):
            yield pytest.param(
                doc["type"], case["value"], case["canonical"], id=f"{path.name}:{case['name']}"
            )


@pytest.mark.parametrize(("kind", "value", "canonical"), list(_tolerated_cases()))
def test_rust_and_python_drop_the_same_unknown_fields(kind, value, canonical):
    """ADR 0012 rule 1: both readers accept extras and agree on what remains."""
    payload = json.dumps(value)
    rust_json = _honba.canonical_json(kind, payload)
    assert json.loads(rust_json) == canonical
    model = wire.loads(kind, payload)
    py_json = TypeAdapter(wire.MODELS[kind]).dump_json(model).decode()
    assert json.loads(py_json) == canonical
    assert _honba.canonical_json(kind, py_json) == rust_json


def _text_cases():
    for path in sorted(GOLDEN.glob("*.json")):
        doc = json.loads(path.read_text())
        for case in doc.get("invalid_text", []):
            yield pytest.param(doc["type"], case["text"], id=f"{path.name}:{case['name']}")


@pytest.mark.parametrize(("kind", "text"), list(_text_cases()))
def test_rust_and_python_reject_the_same_raw_text(kind, text):
    with pytest.raises(ValueError):
        _honba.canonical_json(kind, text)
    with pytest.raises(ValueError):
        wire.loads(kind, text)


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


#: Variants that exist only in Python until Rust gains them (parity pending). Each entry is a
#: deliberate, reviewed exception; remove it when the Rust enum has the variant.
PYTHON_ONLY_VARIANTS: dict[str, frozenset[str]] = {"OrderType": frozenset({"trailing_stop"})}


def test_rust_and_python_wire_enums_have_the_same_variants():
    """A variant added on either side without the other fails here."""
    rust = _honba.wire_enum_values()
    assert set(rust) == set(wire.ENUMS)
    for name, enum in wire.ENUMS.items():
        python_only = PYTHON_ONLY_VARIANTS.get(name, frozenset())
        python = sorted(member.value for member in enum if member.value not in python_only)
        assert sorted(rust[name]) == python, name


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        pytest.param(name, member.value, id=f"{name}.{member.name}")
        for name, enum in wire.ENUMS.items()
        for member in enum
        if member.value not in PYTHON_ONLY_VARIANTS.get(name, frozenset())
    ],
)
def test_every_python_enum_value_parses_in_rust(kind, value):
    payload = json.dumps(value)
    assert _honba.canonical_json(kind, payload) == payload
