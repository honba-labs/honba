"""Unit tests for screener presets and lookback metadata (Design.md Section 5.5 & 12.3)."""

from __future__ import annotations

from honba.entities.screener import FilterOp, MetricRef, ScreenerFilterPredicate
from honba.query.parser import parse_filters
from honba.screener.catalog import load_catalog
from honba.screener.presets import (
    expand_preset,
    get_lookback_bars,
    list_presets,
)


def test_list_presets():
    presets = list_presets()
    assert "52_week_low" in presets
    assert "52_week_high" in presets
    p_low = presets["52_week_low"]
    assert p_low.label == "52 Week Low"
    assert "52 week low" in p_low.aliases


def test_lookback_bars():
    assert get_lookback_bars("price_52_week_low") == 252
    assert get_lookback_bars("price_52_week_high") == 252
    assert get_lookback_bars("SMA200") == 200
    assert get_lookback_bars("SMA50") == 50
    assert get_lookback_bars("SMA20") == 20
    assert get_lookback_bars("RSI") == 14
    assert get_lookback_bars("close") == 1


def test_expand_at_52_week_low():
    preds = expand_preset("at", "52_week_low")
    assert len(preds) == 1
    p = preds[0]
    assert p.key == "close"
    assert p.op == FilterOp.LTE
    # In facts mode: close <= price_52_week_low
    assert p.value == MetricRef(key="price_52_week_low")


def test_expand_near_52_week_low():
    preds = expand_preset("near", "52_week_low", tolerance_pct=5.0)
    assert len(preds) == 1
    p = preds[0]
    assert p.key == "close"
    assert p.op == FilterOp.LTE
    assert p.value == MetricRef(key="price_52_week_low")


def test_expand_at_52_week_high():
    preds = expand_preset("at", "52_week_high")
    assert len(preds) == 1
    p = preds[0]
    assert p.key == "close"
    assert p.op == FilterOp.GTE
    assert p.value == MetricRef(key="price_52_week_high")


def test_parser_integration_with_presets():
    catalog = load_catalog()
    # "close at 52 week low"
    group = parse_filters("close at 52 week low", catalog=catalog, market="india")
    assert len(group.items) == 1
    p = group.items[0]
    assert isinstance(p, ScreenerFilterPredicate)
    assert p.key == "close"
    assert p.op == FilterOp.LTE
    assert p.value.key == "price_52_week_low"
