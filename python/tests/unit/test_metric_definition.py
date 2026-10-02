"""Unit tests for ``MetricDefinition`` catalog fields (``uiId``, ``aliases``)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from honba.entities.screener import MetricDefinition, UnitType, ValueType

WIRE = {
    "key": "price_52_week_low",
    "uiId": "low52",
    "label": "52W Low",
    "group": "OVERVIEW",
    "valueType": "MONEY",
    "unit": "PRICE",
    "source": "MARKET",
    "aliases": ["52 week low", "52w low"],
}


def test_ui_id_and_aliases_are_parsed_from_camel_case():
    m = MetricDefinition.model_validate(WIRE)
    assert m.key == "price_52_week_low"
    assert m.ui_id == "low52"
    assert m.aliases == ["52 week low", "52w low"]
    assert m.value_type is ValueType.MONEY
    assert m.unit is UnitType.PRICE


def test_camel_case_round_trip():
    m = MetricDefinition.model_validate(WIRE)
    dumped = m.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    assert dumped == WIRE
    assert MetricDefinition.model_validate(dumped) == m


def test_ui_id_and_aliases_are_optional():
    m = MetricDefinition.model_validate(
        {"key": "close", "label": "Price", "group": "OVERVIEW", "valueType": "MONEY"}
    )
    assert m.ui_id is None
    assert m.aliases == []


@pytest.mark.parametrize(
    "patch",
    [
        {"uiId": 5},
        {"aliases": "52w low"},
        {"aliases": ["ok", 3]},
        {"ui_id": "low52"},
        {"unknownField": 1},
    ],
)
def test_invalid_catalog_fields_are_rejected(patch):
    with pytest.raises(ValidationError):
        MetricDefinition.model_validate({**WIRE, **patch})
