"""Unit tests for the filter language grammar and parser (Design.md Section 5).

Tests written first (TDD, red -> green).
"""

from __future__ import annotations

import pytest

from honba.entities.screener import (
    FilterOp,
    MetricDefinition,
    MetricPeriod,
    MetricRef,
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
    Timeframe,
    UnitType,
    ValueType,
)
from honba.query.parser import FilterParseError, parse_filters
from honba.screener.catalog import MetricCatalog


def _m(
    key: str,
    ui_id: str | None = None,
    aliases=(),
    group: str = "OVERVIEW",
    unit: UnitType | None = None,
    value_type: ValueType = ValueType.NUMBER,
    has_timeframe: bool = False,
    has_period: bool = False,
) -> MetricDefinition:
    data = {
        "key": key,
        "label": key,
        "group": group,
        "valueType": value_type.value,
        "aliases": list(aliases),
        "hasTimeframe": has_timeframe,
        "hasPeriod": has_period,
    }
    if ui_id:
        data["uiId"] = ui_id
    if unit:
        data["unit"] = unit.value
    return MetricDefinition.model_validate(data)


@pytest.fixture
def catalog() -> MetricCatalog:
    return MetricCatalog(
        [
            _m(
                "market_cap_basic",
                "marketCap",
                ["market cap", "mcap"],
                unit=UnitType.CURRENCY,
                value_type=ValueType.MONEY,
            ),
            _m("close", "price", ["last price", "ltp"], unit=UnitType.PRICE, has_timeframe=True),
            _m("volume", "volume", ["vol"], unit=UnitType.SHARES, has_timeframe=True),
            _m(
                "RSI",
                "rsi14",
                ["rsi", "relative strength index"],
                group="TECHNICALS",
                has_timeframe=True,
            ),
            _m(
                "SMA20",
                "sma20",
                ["20 day sma", "20 sma"],
                group="TECHNICALS",
                unit=UnitType.PRICE,
                has_timeframe=True,
            ),
            _m(
                "SMA50",
                "sma50",
                ["50 day sma", "50 sma"],
                group="TECHNICALS",
                unit=UnitType.PRICE,
                has_timeframe=True,
            ),
            _m(
                "SMA200",
                "sma200",
                ["200 day sma", "200 sma", "200 day moving average"],
                group="TECHNICALS",
                unit=UnitType.PRICE,
                has_timeframe=True,
            ),
            _m(
                "price_earnings_ttm",
                "pe",
                ["pe ratio", "p/e", "pe"],
                unit=UnitType.RATIO,
                has_period=True,
            ),
            _m("delivery_pct", "deliveryPct", ["delivery pct", "delivery %"], unit=UnitType.PCT),
            _m(
                "sector",
                "sector",
                ["industry sector"],
                group="SECURITY",
                value_type=ValueType.ENUM,
            ),
            _m(
                "exchange",
                "exchange",
                ["exchange"],
                group="SECURITY",
                value_type=ValueType.ENUM,
            ),
            _m(
                "description",
                "name",
                ["company name"],
                group="SECURITY",
                value_type=ValueType.STRING,
            ),
        ]
    )


# --- 1. Basic comparisons ---------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected_op", "expected_val"),
    [
        ("market cap above 10000 Cr", FilterOp.GT, 100_000_000_000.0),
        ("market cap over 10000 Cr", FilterOp.GT, 100_000_000_000.0),
        ("market cap greater than 10000 Cr", FilterOp.GT, 100_000_000_000.0),
        ("market cap at least 5000 Cr", FilterOp.GTE, 50_000_000_000.0),
        ("rsi below 30", FilterOp.LT, 30.0),
        ("rsi under 30", FilterOp.LT, 30.0),
        ("rsi less than 30", FilterOp.LT, 30.0),
        ("pe ratio at most 25", FilterOp.LTE, 25.0),
        ("pe ratio no more than 25", FilterOp.LTE, 25.0),
        ("rsi equals 50", FilterOp.EQ, 50.0),
        ("rsi is 50", FilterOp.EQ, 50.0),
        ("rsi != 50", FilterOp.NEQ, 50.0),
        ("rsi is not 50", FilterOp.NEQ, 50.0),
    ],
)
def test_comparison_phrases(catalog, phrase, expected_op, expected_val):
    group = parse_filters(phrase, catalog=catalog, market="india")
    assert len(group.items) == 1
    pred = group.items[0]
    assert isinstance(pred, ScreenerFilterPredicate)
    assert pred.op == expected_op
    assert pred.value == pytest.approx(expected_val)


# --- 2. Between X and Y ------------------------------------------------------


def test_between_operator(catalog):
    group = parse_filters("market cap between 5000 Cr and 2 Tn", catalog=catalog, market="india")
    assert len(group.items) == 1
    pred = group.items[0]
    assert pred.key == "market_cap_basic"
    assert pred.op == FilterOp.BETWEEN
    assert pred.value == [50_000_000_000.0, 2_000_000_000_000.0]


# --- 3. IN and NOT IN --------------------------------------------------------


def test_in_and_not_in(catalog):
    group1 = parse_filters("sector in IT, Banks", catalog=catalog, market="india")
    pred1 = group1.items[0]
    assert pred1.key == "sector"
    assert pred1.op == FilterOp.IN
    assert pred1.value == ["IT", "Banks"]

    group2 = parse_filters("exchange not in BSE", catalog=catalog, market="india")
    pred2 = group2.items[0]
    assert pred2.key == "exchange"
    assert pred2.op == FilterOp.NOT_IN
    assert pred2.value == ["BSE"]


# --- 4. LIKE and CONTAINS ----------------------------------------------------


def test_contains_and_like(catalog):
    group = parse_filters("company name contains Tata", catalog=catalog, market="india")
    pred = group.items[0]
    assert pred.key == "description"
    assert pred.op in (FilterOp.LIKE, FilterOp.HAS)
    assert pred.value == "Tata"


# --- 5. Crossovers (metric to metric operand) --------------------------------


def test_crossovers(catalog):
    group = parse_filters("50 day sma crosses above 200 day sma", catalog=catalog, market="india")
    pred = group.items[0]
    assert pred.key == "SMA50"
    assert pred.op == FilterOp.CROSSES_ABOVE
    assert pred.value == MetricRef(key="SMA200")


# --- 6. Timeframe and Period overrides ---------------------------------------


def test_timeframe_and_period(catalog):
    group = parse_filters("rsi on 1d above 70", catalog=catalog, market="india")
    pred = group.items[0]
    assert pred.key == "RSI"
    assert pred.timeframe == Timeframe.D1

    group_p = parse_filters("pe ratio for ttm under 20", catalog=catalog, market="india")
    pred_p = group_p.items[0]
    assert pred_p.key == "price_earnings_ttm"
    assert pred_p.period == MetricPeriod.TTM


def test_timeframe_on_metric_without_timeframe_raises_error(catalog):
    with pytest.raises(FilterParseError, match="timeframe"):
        parse_filters("market cap on 1d above 1000 Cr", catalog=catalog, market="india")


def test_period_on_metric_without_period_raises_error(catalog):
    with pytest.raises(FilterParseError, match="period"):
        parse_filters("rsi for ttm above 70", catalog=catalog, market="india")


# --- 7. Logical operator precedence and grouping -----------------------------


def test_and_binds_tighter_than_or(catalog):
    # A or B and C => A or (B and C)
    text = "rsi below 30 or rsi above 70 and volume at least 5 Lk"
    group = parse_filters(text, catalog=catalog, market="india")
    assert group.operator == "OR"
    assert len(group.items) == 2

    first = group.items[0]
    assert isinstance(first, ScreenerFilterPredicate)
    assert first.key == "RSI"
    assert first.op == FilterOp.LT

    second = group.items[1]
    assert isinstance(second, ScreenerFilterGroup)
    assert second.operator == "AND"
    assert len(second.items) == 2


def test_either_or_end_grouping(catalog):
    # either A or B end and C => (A or B) and C
    text = "either rsi below 30 or rsi above 70 end and volume at least 5 Lk"
    group = parse_filters(text, catalog=catalog, market="india")
    assert group.operator == "AND"
    assert len(group.items) == 2

    first = group.items[0]
    assert isinstance(first, ScreenerFilterGroup)
    assert first.operator == "OR"

    second = group.items[1]
    assert isinstance(second, ScreenerFilterPredicate)
    assert second.key == "volume"


# --- 8. Error carets and position reporting -----------------------------------


def test_error_caret_reporting(catalog):
    with pytest.raises(FilterParseError) as exc_info:
        parse_filters("rsi below 30 Cr", catalog=catalog, market="india")
    err = str(exc_info.value)
    assert "^" in err
    assert "Cr" in err
