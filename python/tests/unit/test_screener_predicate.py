"""Unit tests for ``MetricRef`` and the ``ScreenerFilterPredicate.value`` contract.

The same contract is enforced in Rust (``honba_entities::screener``) and shared
through ``schema/golden/screener_predicate.json``.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from honba.entities.screener import (
    FilterOp,
    MetricPeriod,
    MetricRef,
    ScreenerFilterPredicate,
    Timeframe,
)


def _pred(op: str, value, **extra) -> ScreenerFilterPredicate:
    return ScreenerFilterPredicate.model_validate(
        {"key": "SMA50", "op": op, "value": value, **extra}
    )


# --- MetricRef ---------------------------------------------------------------


def test_metric_ref_serialises_key_only_when_dimensions_absent():
    ref = MetricRef(key="SMA200")
    assert ref.model_dump(mode="json") == {"key": "SMA200"}
    assert ref.model_dump(mode="json", by_alias=True) == {"key": "SMA200"}
    assert ref.model_dump_json() == '{"key":"SMA200"}'


def test_metric_ref_round_trips_period_and_timeframe():
    wire = {"key": "price_earnings_ttm", "period": "TTM", "timeframe": "1W"}
    ref = MetricRef.model_validate(wire)
    assert ref.period is MetricPeriod.TTM
    assert ref.timeframe is Timeframe.W1
    assert ref.model_dump(mode="json", by_alias=True) == wire


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"key": 5},
        {"key": "SMA200", "extra": 1},
        {"key": "SMA200", "period": "WEEKLY"},
        {"key": "SMA200", "timeframe": "1d"},
    ],
)
def test_metric_ref_rejects_invalid_payloads(bad):
    with pytest.raises(ValidationError):
        MetricRef.model_validate(bad)


# --- predicate value contract: valid ----------------------------------------


@pytest.mark.parametrize("op", ["crosses_above", "crosses_below", "gt", "gte", "lt", "lte"])
def test_metric_ref_operand_is_parsed_for_comparison_ops(op):
    pred = _pred(op, {"key": "SMA200", "timeframe": "1D"})
    assert pred.value == MetricRef(key="SMA200", timeframe=Timeframe.D1)
    assert pred.model_dump(mode="json")["value"] == {"key": "SMA200", "timeframe": "1D"}


@pytest.mark.parametrize("op", ["crosses_above", "crosses_below", "gt", "gte", "lt", "lte"])
@pytest.mark.parametrize("number", [0, 50, -1.5, 10000000000])
def test_numeric_threshold_is_accepted(op, number):
    assert _pred(op, number).value == number


@pytest.mark.parametrize("op", ["gt", "gte", "lt", "lte"])
def test_ordering_ops_still_accept_string_scalars(op):
    assert _pred(op, "2024-01-01").value == "2024-01-01"


def test_between_takes_two_finite_numbers():
    assert _pred("between", [10, 20.5]).value == [10, 20.5]


@pytest.mark.parametrize("op", ["in", "not_in"])
@pytest.mark.parametrize("value", [[], ["NSE", "BSE"], [1, 2, 3]])
def test_set_ops_take_a_list(op, value):
    assert _pred(op, value).value == value


@pytest.mark.parametrize("op", ["eq", "neq", "like", "has"])
@pytest.mark.parametrize("value", ["NSE", 1, True, None, ["a"], {"any": "thing"}])
def test_unconstrained_ops_keep_any_value(op, value):
    assert _pred(op, value).value == value


def test_crossing_two_metrics_serialises_as_wire_json():
    pred = ScreenerFilterPredicate(
        key="SMA50",
        op=FilterOp.CROSSES_ABOVE,
        value=MetricRef(key="SMA200"),
        timeframe=Timeframe.D1,
    )
    assert pred.model_dump(mode="json", by_alias=True) == {
        "key": "SMA50",
        "op": "crosses_above",
        "value": {"key": "SMA200"},
        "timeframe": "1D",
    }


def test_absent_dimensions_are_omitted_like_rust():
    assert _pred("gt", 5).model_dump(mode="json") == {"key": "SMA50", "op": "gt", "value": 5}


# --- predicate value contract: invalid --------------------------------------

INVALID = [
    ("crosses_above", "200"),
    ("crosses_above", True),
    ("crosses_above", None),
    ("crosses_above", [1, 2]),
    ("crosses_below", {"key": "SMA200", "bogus": 1}),
    ("crosses_below", {"period": "TTM"}),
    ("crosses_above", math.nan),
    ("crosses_above", math.inf),
    ("gt", None),
    ("gt", True),
    ("gt", [1]),
    ("gte", {"key": 200}),
    ("lt", -math.inf),
    ("between", [1]),
    ("between", [1, 2, 3]),
    ("between", 5),
    ("between", [1, "2"]),
    ("between", [1, True]),
    ("between", [1, math.nan]),
    ("between", {"key": "SMA200"}),
    ("in", "NSE"),
    ("in", {"key": "SMA200"}),
    ("not_in", 5),
]


@pytest.mark.parametrize(("op", "value"), INVALID)
def test_value_contract_violations_are_rejected(op, value):
    with pytest.raises(ValidationError):
        _pred(op, value)


def test_value_is_required():
    with pytest.raises(ValidationError):
        ScreenerFilterPredicate.model_validate({"key": "SMA50", "op": "gt"})


def test_schema_bundle_exports_metric_ref(tmp_path):
    import json

    from honba.cli._schema_export import export_json_schema

    bundle = json.loads(export_json_schema(tmp_path).read_text())
    assert bundle["properties"]["metric_ref"] == {"$ref": "#/$defs/MetricRef"}
    assert set(bundle["$defs"]["MetricRef"]["properties"]) == {"key", "period", "timeframe"}
