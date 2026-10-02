"""Unit tests for quantity parsing and unit validation (Section 6 of Design.md).

Tests written first (TDD, red -> green).
"""

from __future__ import annotations

import pytest

from honba.entities.screener import MetricDefinition, UnitType, ValueType
from honba.query.quantity import (
    QuantityError,
    UnitMismatchError,
    parse_quantity,
    validate_quantity_for_metric,
)


def _make_metric(
    key: str,
    unit: UnitType | None = None,
    value_type: ValueType = ValueType.NUMBER,
) -> MetricDefinition:
    data = {
        "key": key,
        "label": key,
        "group": "OVERVIEW",
        "valueType": value_type.value,
    }
    if unit is not None:
        data["unit"] = unit.value
    return MetricDefinition.model_validate(data)


# --- 1. Basic numeric and decimal parsing -------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_val"),
    [
        ("0", 0.0),
        ("42", 42.0),
        ("-15", -15.0),
        ("+10.5", 10.5),
        ("3.14159", 3.14159),
        (".5", 0.5),
        ("1000", 1000.0),
    ],
)
def test_parse_plain_numbers(text: str, expected_val: float):
    q = parse_quantity(text)
    assert q.value == pytest.approx(expected_val)
    assert q.unit_suffix is None
    assert q.currency is None


# --- 2. Shared multipliers and aliases ----------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_val", "expected_suffix"),
    [
        ("10k", 10_000.0, "K"),
        ("10 K", 10_000.0, "K"),
        ("5 thousand", 5_000.0, "K"),
        ("2.5mn", 2_500_000.0, "Mn"),
        ("2.5 Mn", 2_500_000.0, "Mn"),
        ("10 million", 10_000_000.0, "Mn"),
        ("1.2bn", 1_200_000_000.0, "Bn"),
        ("1.2 Bn", 1_200_000_000.0, "Bn"),
        ("3 billion", 3_000_000_000.0, "Bn"),
        ("2tn", 2_000_000_000_000.0, "Tn"),
        ("2 Tn", 2_000_000_000_000.0, "Tn"),
        ("1.5 trillion", 1_500_000_000_000.0, "Tn"),
    ],
)
def test_parse_shared_multipliers(text: str, expected_val: float, expected_suffix: str):
    q = parse_quantity(text)
    assert q.value == pytest.approx(expected_val)
    assert q.unit_suffix == expected_suffix


# --- 3. India market suffixes: Lk, L, Cr --------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_val", "expected_suffix"),
    [
        ("5Lk", 500_000.0, "Lk"),
        ("5 Lk", 500_000.0, "Lk"),
        ("5L", 500_000.0, "Lk"),
        ("5 L", 500_000.0, "Lk"),
        ("10 lakh", 1_000_000.0, "Lk"),
        ("10 lakhs", 1_000_000.0, "Lk"),
        ("10 lac", 1_000_000.0, "Lk"),
        ("5000 Cr", 50_000_000_000.0, "Cr"),
        ("5000Cr", 50_000_000_000.0, "Cr"),
        ("1.1 cr", 11_000_000.0, "Cr"),
        ("2 crore", 20_000_000.0, "Cr"),
        ("2 crores", 20_000_000.0, "Cr"),
    ],
)
def test_parse_india_market_multipliers(text: str, expected_val: float, expected_suffix: str):
    q = parse_quantity(text, market="india")
    assert q.value == pytest.approx(expected_val)
    assert q.unit_suffix == expected_suffix


# --- 4. Percentages -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_val"),
    [
        ("15%", 15.0),
        ("15 %", 15.0),
        ("15 percent", 15.0),
        ("0.5%", 0.5),
        ("-3.2%", -3.2),
    ],
)
def test_parse_percentages(text: str, expected_val: float):
    # Note: in screener metrics, percentage metrics (PCT) typically expect the percentage number (e.g. 15 for 15%)
    # Design.md specifies: % has multiplier 1e-2 or represents percent unit
    q = parse_quantity(text)
    assert q.unit_suffix == "%"
    assert q.raw_number == pytest.approx(expected_val)


# --- 5. Currency markers ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_currency", "expected_val"),
    [
        ("₹500", "INR", 500.0),
        ("₹ 500", "INR", 500.0),
        ("Rs 1000", "INR", 1000.0),
        ("Rs. 1000", "INR", 1000.0),
        ("1000 INR", "INR", 1000.0),
        ("₹ 10 Cr", "INR", 100_000_000.0),
    ],
)
def test_parse_currency_markers(text: str, expected_currency: str, expected_val: float):
    q = parse_quantity(text, market="india")
    assert q.currency == expected_currency
    assert q.value == pytest.approx(expected_val)


def test_reject_unsupported_currency_for_india():
    with pytest.raises(QuantityError, match="currency"):
        parse_quantity("$500", market="india")


# --- 6. Metric Unit Compatibility (Section 6.2) -------------------------------


def test_money_metric_accepts_money_multipliers_and_currencies():
    mcap = _make_metric("market_cap_basic", unit=UnitType.CURRENCY, value_type=ValueType.MONEY)
    q1 = parse_quantity("5000 Cr", market="india")
    validate_quantity_for_metric(q1, mcap)

    q2 = parse_quantity("₹ 1000", market="india")
    validate_quantity_for_metric(q2, mcap)


def test_shares_metric_accepts_multipliers_without_currency():
    volume = _make_metric("volume", unit=UnitType.SHARES, value_type=ValueType.NUMBER)
    q1 = parse_quantity("5 Lk", market="india")
    validate_quantity_for_metric(q1, volume)

    # Volume cannot have a currency symbol
    q_curr = parse_quantity("₹ 5 Lk", market="india")
    with pytest.raises(UnitMismatchError, match="currency"):
        validate_quantity_for_metric(q_curr, volume)


def test_percentage_metric_accepts_percent_and_raw_numbers():
    delivery_pct = _make_metric("delivery_pct", unit=UnitType.PCT, value_type=ValueType.NUMBER)
    q_pct = parse_quantity("50%")
    validate_quantity_for_metric(q_pct, delivery_pct)

    q_num = parse_quantity("50")
    validate_quantity_for_metric(q_num, delivery_pct)

    # Cannot pass Crore/Lakh to percent metric
    q_cr = parse_quantity("50 Cr", market="india")
    with pytest.raises(UnitMismatchError, match="unit"):
        validate_quantity_for_metric(q_cr, delivery_pct)


def test_rsi_rejects_money_suffix():
    rsi = _make_metric("RSI", unit=None, value_type=ValueType.NUMBER)
    q = parse_quantity("30 Cr", market="india")
    with pytest.raises(UnitMismatchError, match="unit"):
        validate_quantity_for_metric(q, rsi)
