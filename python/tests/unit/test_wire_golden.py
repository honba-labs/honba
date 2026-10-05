"""Golden-vector contract tests for the Python wire models (ADR 006).

Reads the same ``schema/golden/*.json`` files as the Rust tests in
``crates/*/tests/golden.rs``. Pure Python: no native extension needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from honba.entities import wire

GOLDEN = Path(__file__).resolve().parents[3] / "schema" / "golden"

ADAPTERS: dict[str, TypeAdapter] = {name: TypeAdapter(model) for name, model in wire.MODELS.items()}


def _load(file: str) -> dict:
    return json.loads((GOLDEN / file).read_text())


FILES = sorted(p.name for p in GOLDEN.glob("*.json"))


def _cases(key: str):
    for file in FILES:
        doc = _load(file)
        for case in doc.get(key, []):
            yield pytest.param(doc["type"], case["value"], id=f"{file}:{case['name']}")


def _text_cases():
    for file in FILES:
        doc = _load(file)
        for case in doc.get("invalid_text", []):
            yield pytest.param(doc["type"], case["text"], id=f"{file}:{case['name']}")


def test_golden_files_exist_for_every_model():
    types = {_load(f)["type"] for f in FILES}
    assert types == set(wire.MODELS)


@pytest.mark.parametrize("file", FILES)
def test_golden_files_match_schema_version(file):
    assert _load(file)["schema_version"] == wire.SCHEMA_VERSION


@pytest.mark.parametrize(("kind", "value"), list(_cases("cases")))
def test_golden_case_roundtrips(kind, value):
    adapter = ADAPTERS[kind]
    model = adapter.validate_python(value)
    assert adapter.dump_python(model, mode="json") == value
    # Text round trip through JSON, as another process would see it.
    again = adapter.validate_json(adapter.dump_json(model))
    assert again == model


@pytest.mark.parametrize(("kind", "value"), list(_cases("cases")))
def test_golden_case_parses_from_text(kind, value):
    assert wire.loads(kind, json.dumps(value)) == ADAPTERS[kind].validate_python(value)


@pytest.mark.parametrize(("kind", "value"), list(_cases("invalid")))
def test_golden_invalid_case_rejected(kind, value):
    with pytest.raises(ValidationError):
        ADAPTERS[kind].validate_python(value)
    with pytest.raises(ValueError):
        wire.loads(kind, json.dumps(value))


def test_golden_has_raw_text_cases():
    assert list(_text_cases())


@pytest.mark.parametrize(("kind", "text"), list(_text_cases()))
def test_golden_invalid_text_rejected(kind, text):
    with pytest.raises(ValueError):
        wire.loads(kind, text)


@pytest.mark.parametrize(
    "text",
    [
        '{"symbol": "A", "exchange": "NSE", "symbol": "B"}',
        '{"symbol": "A", "exchange": {"x": 1, "x": 2}}',
    ],
)
def test_loads_rejects_duplicate_keys_at_any_depth(text):
    with pytest.raises(ValueError, match="duplicate key"):
        wire.loads("InstrumentId", text)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_loads_rejects_non_finite_numbers(literal):
    text = (
        '{"type": "order_filled", "order_id": "O-1", "last_qty": 1.0, '
        f'"last_px": {literal}, "ts_event": 1}}'
    )
    with pytest.raises(ValueError):
        wire.loads("Event", text)


def test_loads_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown wire kind"):
        wire.loads("Nope", "{}")


def test_event_ids_are_order_ids():
    ev = ADAPTERS["Event"].validate_python(
        {"type": "order_accepted", "order_id": "O-1", "ts_event": {"iso": "1970-01-01T00:00:00.000000005Z", "unix_nanos": "5"}}
    )
    assert isinstance(ev, wire.OrderAccepted)
    assert ev.order_id == "O-1"


def test_message_wrap_sets_current_schema_version():
    ev = wire.OrderCancelled(order_id="O-3", ts_event={"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"})
    msg = wire.Message.wrap(ev, ts_init=2)
    assert msg.schema_version == wire.SCHEMA_VERSION
    assert msg.model_dump(mode="json")["event"]["type"] == "order_cancelled"


def test_wire_rejects_non_finite_and_negative_timestamps():
    nifty = {"symbol": "NIFTY50", "exchange": "NSE"}
    with pytest.raises(ValidationError):
        wire.QuoteTick(
            instrument_id=nifty,
            bid_price=float("nan"),
            ask_price=1.0,
            bid_size=1.0,
            ask_size=1.0,
            ts_event=0,
            ts_init=0,
        )
    with pytest.raises(ValidationError):
        wire.OrderAccepted(order_id="O", ts_event=-1)
    with pytest.raises(ValidationError):
        wire.OrderAccepted(order_id="O", ts_event=2**64)
