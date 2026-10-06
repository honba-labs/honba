"""Parity of the Python ATR with the shared ``ohlc_series`` golden vectors.

``schema/conformance/ohlc_series.json`` is executed by
``crates/honba-api-wasm/tests/ohlc_conformance.rs`` against the Rust ATR behind the WASM
``ohlc_indicator_series`` export, and by the node harness through the real wasm build. The expected
values were generated from the Python ``Atr``; running them here keeps that true.

The WASM/Rust ATR uses the first bar's high - low as its first true range, which is the Python
``include_first_bar=True`` variant (the Python default skips the first bar and warms up one bar
longer), so the vectors are driven with that flag.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from honba.strategies.indicators import build_indicator

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "schema" / "conformance" / "ohlc_series.json").read_text(
        encoding="utf-8"
    )
)
REL = FIXTURE["tolerance"]["relative"]


def _close(actual, expected) -> bool:
    if expected is None:
        return actual is None or (isinstance(actual, float) and math.isnan(actual))
    if actual is None:
        return False
    if float(expected).is_integer():
        return actual == expected
    return abs(actual - expected) <= REL * max(1.0, abs(expected))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_python_atr_matches_golden_vectors(case):
    assert case["indicator"] == "atr"
    ind = build_indicator("atr", include_first_bar=True, **case["params"])
    bars = FIXTURE["inputs"][case["input"]]
    actual = [ind.update(h, lo, c) for h, lo, c in zip(bars["high"], bars["low"], bars["close"])]
    assert len(actual) == len(case["expected"])
    for i, (a, e) in enumerate(zip(actual, case["expected"])):
        assert _close(a, e), f"{case['name']}[{i}]: got {a}, want {e}"
