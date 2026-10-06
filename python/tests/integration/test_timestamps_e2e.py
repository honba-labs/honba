"""E11-S2 end to end: timestamps cross JSON as ``{iso, unix_nanos}`` everywhere.

Rust writes, Python reads (and the reverse) every vector in
``schema/golden/unix_nanos.json`` without losing a nanosecond, including values past
2^53 where a JSON number would round; and the generated TypeScript and ``.pyi``
surfaces type the field as two strings, never a number.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from honba import wire

_honba = pytest.importorskip("honba._honba")

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[3] / "schema" / "golden" / "unix_nanos.json").read_text()
)
CASES = [pytest.param(c["value"], id=c["name"]) for c in GOLDEN["cases"]]


def test_the_vectors_cover_precision_and_u64_edges() -> None:
    values = {int(c["value"]["unix_nanos"]) for c in GOLDEN["cases"]}
    assert {0, 2**53, 2**53 + 1, 2**64 - 1} <= values
    # 2^53 + 1 is the first integer a JSON (f64) number cannot carry exactly.
    assert float(2**53 + 1) == float(2**53)


@pytest.mark.parametrize("golden", CASES)
def test_rust_written_timestamps_read_exactly_in_python(golden: dict) -> None:
    rust_json = _honba.canonical_json("UnixNanos", json.dumps(golden))
    ts = wire.loads("UnixNanos", rust_json)
    assert ts.to_ns() == int(golden["unix_nanos"])
    assert ts.model_dump(mode="json") == golden


@pytest.mark.parametrize("golden", CASES)
def test_python_written_timestamps_read_exactly_in_rust(golden: dict) -> None:
    ns = int(golden["unix_nanos"])
    py_json = wire.UnixNanos.from_ns(ns).model_dump_json()
    assert json.loads(_honba.canonical_json("UnixNanos", py_json)) == golden


def test_pre_epoch_is_rejected_by_both_writers_and_readers() -> None:
    with pytest.raises(ValueError):
        wire.UnixNanos.from_ns(-1)
    bad = json.dumps({"iso": "1969-12-31T23:59:59.999999999Z", "unix_nanos": "-1"})
    with pytest.raises(ValueError):
        _honba.canonical_json("UnixNanos", bad)
    with pytest.raises(ValueError):
        wire.loads("UnixNanos", bad)


def test_a_bar_past_two_pow_53_round_trips_through_both_languages() -> None:
    ns = 2**53 + 1
    bar = {
        "bar_type": {
            "instrument_id": {"symbol": "X", "exchange": "NSE"},
            "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
        },
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 0.0,
        "ts_event": wire.UnixNanos.from_ns(ns).model_dump(),
        "ts_init": wire.UnixNanos.from_ns(ns + 1).model_dump(),
    }
    back = wire.loads("Bar", _honba.canonical_json("Bar", json.dumps(bar)))
    assert (back.ts_event.to_ns(), back.ts_init.to_ns()) == (ns, ns + 1)


def test_generated_typescript_types_timestamps_as_two_strings() -> None:
    _, ts = _honba.codegen_render("typescript")
    block = re.search(r"export interface UnixNanos \{([^}]*)\}", ts)
    assert block is not None
    fields = {f.strip() for f in block.group(1).split(";") if f.strip()}
    assert fields == {"iso: string", "unix_nanos: string"}
    # Every ts_* field references the object type; none is a bare number.
    assert re.findall(r"\bts_\w+\??: (\w+)", ts)
    assert set(re.findall(r"\bts_\w+\??: (\w+)", ts)) == {"UnixNanos"}


def test_generated_python_stub_types_timestamps_as_two_strings() -> None:
    _, pyi = _honba.codegen_render("pyi")
    block = re.search(r"class UnixNanos:\n((?:    .*\n)+)", pyi)
    assert block is not None
    assert block.group(1).split() == ["iso:", "str", "unix_nanos:", "str"]
