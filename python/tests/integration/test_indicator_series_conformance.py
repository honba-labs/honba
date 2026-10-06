"""Parity of the Python indicators with the shared ``indicator_series`` golden vectors.

``schema/conformance/indicator_series.json`` is executed by
``crates/honba-api-wasm/tests/indicator_conformance.rs`` against the Rust indicators that back the
WASM ``indicator_series`` export. Running the same vectors through the Python indicators proves
the two implementations agree. (``honba._honba`` does not bind indicators yet, so the Python
reference implementation is the Python-side surface.)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from honba.strategies.indicators import build_indicator

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3] / "schema" / "conformance" / "indicator_series.json"
    ).read_text(encoding="utf-8")
)
REL = FIXTURE["tolerance"]["relative"]


def _build(indicator: str, params: dict):
    """Maps the wasm param names onto the Python indicator, returning (indicator, output key)."""
    params = dict(params)
    output = params.pop("output", None)
    if indicator == "bollinger":
        k = params.pop("k", 2.0)
        return build_indicator("bollinger", mult=k, **params), output or "middle"
    if indicator == "macd":
        return build_indicator("macd", **params), output or "macd"
    return build_indicator(indicator, **params), output


def _pick(value, output):
    if value is None:
        return None
    if output is None:
        return value
    return value[output] if isinstance(value, dict) else getattr(value, output)


def _close(actual, expected) -> bool:
    if expected is None:
        return actual is None or (isinstance(actual, float) and math.isnan(actual))
    if actual is None:
        return False
    if float(expected).is_integer():
        return actual == expected
    return abs(actual - expected) <= REL * max(1.0, abs(expected))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_python_indicators_match_golden_vectors(case):
    ind, output = _build(case["indicator"], case["params"])
    closes = FIXTURE["inputs"][case["input"]]
    actual = [_pick(ind.update(c), output) for c in closes]
    assert len(actual) == len(case["expected"])
    for i, (a, e) in enumerate(zip(actual, case["expected"])):
        assert _close(a, e), f"{case['name']}[{i}]: got {a}, want {e}"
