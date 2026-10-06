"""Parity of the Python screener evaluator with the shared ``screener_scan`` golden vectors.

``schema/conformance/screener_scan.json`` is executed by
``crates/honba-indicators/tests/screener_conformance.rs`` against the Rust evaluator that serves
``GET /screener/scan``. The expected values were generated from this Python evaluator
(``scripts/gen_screener_scan_vectors.py``); running them here keeps that true.

``unsupported`` and ``divergences`` record the cases where Rust deliberately differs; Python's side
of each is pinned too, so a change in the reference is noticed.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from honba.domain.instrument import InstrumentId
from honba.entities.bar import Bar
from honba.entities.screener import ScreenerFilterGroup, ScreenerFilterPredicate
from honba.screener.evaluator import (
    evaluate_group_on_bars,
    evaluate_predicate_on_bars,
    extract_metrics_from_bars,
)

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3] / "schema" / "conformance" / "screener_scan.json"
    ).read_text(encoding="utf-8")
)
REL = FIXTURE["tolerance"]["relative"]


def _bars(name: str) -> list[Bar]:
    s = FIXTURE["inputs"][name]
    iid = InstrumentId("X", "NSE")
    return [
        Bar(iid, i * 86_400_000_000_000, s["open"][i], s["high"][i], s["low"][i], s["close"][i],
            s["volume"][i])
        for i in range(len(s["close"]))
    ]  # fmt: skip


def _outcome(predicate: dict, bars: list[Bar]) -> dict:
    try:
        return {
            "result": evaluate_predicate_on_bars(
                ScreenerFilterPredicate.model_validate(predicate), bars
            )
        }
    except Exception as exc:  # noqa: BLE001 - the reference behaviour is recorded as a name
        return {"error": type(exc).__name__}


def _keys(predicate: dict) -> list[str]:
    keys = [predicate["key"]]
    value = predicate["value"]
    if isinstance(value, dict) and predicate["op"] in (
        "gt",
        "gte",
        "lt",
        "lte",
        "crosses_above",
        "crosses_below",
    ):
        keys.append(value["key"])
    return keys  # fmt: skip


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_python_predicate_matches_golden_vectors(case):
    bars = _bars(case["input"])
    out = _outcome(case["predicate"], bars)
    expected = case["expected"]
    if "error" in expected:
        assert out == {"error": expected["error"]}
        return
    assert out == {"result": expected["result"]}
    metrics = extract_metrics_from_bars(_keys(case["predicate"]), bars)
    assert metrics.keys() == expected["metrics"].keys()
    for key, want in expected["metrics"].items():
        have = metrics[key]
        if want is None:
            assert have is None
        else:
            assert have is not None and math.isclose(have, want, rel_tol=REL), key


@pytest.mark.parametrize("case", FIXTURE["groups"], ids=lambda c: c["name"])
def test_python_group_matches_golden_vectors(case):
    group = ScreenerFilterGroup.model_validate(case["group"])
    assert evaluate_group_on_bars(group, _bars(case["input"])) is case["expected"]["result"]


@pytest.mark.parametrize("case", FIXTURE["unsupported"], ids=lambda c: c["name"])
def test_python_side_of_unsupported_metrics_is_pinned(case):
    assert _outcome(case["predicate"], _bars(case["input"])) == case["python"]


@pytest.mark.parametrize("case", FIXTURE["divergences"], ids=lambda c: c["name"])
def test_python_side_of_divergences_is_pinned(case):
    assert _outcome(case["predicate"], _bars(case["input"])) == case["python"]
