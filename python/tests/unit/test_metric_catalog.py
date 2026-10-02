"""Unit tests for the pure ``honba.screener.MetricCatalog`` (no file I/O)."""

from __future__ import annotations

import pytest

from honba.entities.screener import MetricDefinition
from honba.screener import (
    AmbiguousMetric,
    CatalogError,
    MetricCatalog,
    UnknownMetric,
    normalize_phrase,
)


def metric(key: str, ui_id: str | None = None, aliases=(), group: str = "OVERVIEW"):
    data = {"key": key, "label": key, "group": group, "valueType": "NUMBER"}
    if ui_id is not None:
        data["uiId"] = ui_id
    data["aliases"] = list(aliases)
    return MetricDefinition.model_validate(data)


@pytest.fixture
def catalog() -> MetricCatalog:
    return MetricCatalog(
        [
            metric("close", "price", ["last price"]),
            metric("change", "changePercent", ["change %", "change pct", "pct change"]),
            metric("change_abs", "change", ["absolute change"]),
            metric("price_52_week_low", "low52", ["52 week low", "52w low", "52wk low"]),
            metric("price_52_week_high", "high52", ["52 week high", "52w high"]),
            metric("RSI", "rsi14", ["rsi", "relative strength index"], group="TECHNICALS"),
            metric("SMA200", "sma200", ["200 day moving average"], group="TECHNICALS"),
            metric("market_cap_basic", "marketCap", ["market cap", "mcap"]),
        ]
    )


# --- normalisation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("52W LOW", "52w low"),
        ("52w-low", "52w low"),
        ("  market__cap  ", "market cap"),
        ("change_abs", "change abs"),
        ("Change %", "change %"),
        ("a \t b\n c", "a b c"),
        ("Perf.W", "perf.w"),
    ],
)
def test_normalize_phrase(raw, norm):
    assert normalize_phrase(raw) == norm


# --- resolve -----------------------------------------------------------------


def test_bare_change_is_ambiguous_between_wire_key_and_ui_id(catalog):
    with pytest.raises(AmbiguousMetric) as info:
        catalog.resolve("change")
    assert info.value.candidates == ("change", "change_abs")
    assert "change_abs" in str(info.value)


def test_change_percent_resolves_to_change(catalog):
    assert catalog.resolve("change %").key == "change"


def test_change_abs_resolves_to_change_abs(catalog):
    assert catalog.resolve("change abs").key == "change_abs"


@pytest.mark.parametrize("phrase", ["52w-low", "52 week low", "52W LOW", "low52"])
def test_52_week_low_spellings(catalog, phrase):
    assert catalog.resolve(phrase).key == "price_52_week_low"


@pytest.mark.parametrize(
    ("phrase", "key"), [("rsi", "RSI"), ("sma200", "SMA200"), ("SMA200", "SMA200")]
)
def test_wire_keys_match_case_insensitively(catalog, phrase, key):
    assert catalog.resolve(phrase).key == key


def test_ui_id_resolves_to_wire_key_never_ui_id(catalog):
    resolved = catalog.resolve("marketCap")
    assert resolved.key == "market_cap_basic"
    assert resolved.ui_id == "marketCap"


def test_unknown_phrase_suggests_close_matches(catalog):
    with pytest.raises(UnknownMetric) as info:
        catalog.resolve("market capp")
    assert info.value.phrase == "market capp"
    assert 1 <= len(info.value.suggestions) <= 3
    assert "market_cap_basic" in info.value.suggestions


def test_unknown_phrase_without_close_matches_has_no_suggestions(catalog):
    with pytest.raises(UnknownMetric) as info:
        catalog.resolve("zzzzzz")
    assert info.value.suggestions == ()


def test_errors_are_lookup_errors(catalog):
    with pytest.raises(LookupError):
        catalog.resolve("zzzzzz")
    with pytest.raises(LookupError):
        catalog.resolve("change")


def test_get_by_wire_key_is_exact(catalog):
    assert catalog.get("RSI").ui_id == "rsi14"
    assert catalog.get("rsi") is None
    assert len(catalog) == 8
    assert "SMA200" in catalog
    assert [m.key for m in catalog][:2] == ["close", "change"]


# --- build errors ------------------------------------------------------------


def test_duplicate_alias_across_metrics_is_rejected():
    with pytest.raises(CatalogError, match="alias 'market cap'"):
        MetricCatalog([metric("a", "a1", ["Market Cap"]), metric("b", "b1", ["market_cap"])])


def test_alias_equal_to_another_metrics_wire_key_is_rejected():
    with pytest.raises(CatalogError, match="'close'"):
        MetricCatalog([metric("close", "price"), metric("open", "openPrice", ["Close"])])


def test_alias_equal_to_another_metrics_ui_id_is_rejected():
    with pytest.raises(CatalogError, match="'price'"):
        MetricCatalog([metric("close", "price"), metric("open", "openPrice", ["price"])])


def test_alias_equal_to_own_key_or_ui_id_is_allowed():
    cat = MetricCatalog([metric("RSI", "rsi14", ["rsi", "RSI 14"])])
    assert cat.resolve("rsi").key == "RSI"


def test_duplicate_wire_key_is_rejected():
    with pytest.raises(CatalogError, match="key 'close'"):
        MetricCatalog([metric("close", "a"), metric("close", "b")])


def test_duplicate_ui_id_is_rejected():
    with pytest.raises(CatalogError, match="uiId 'price'"):
        MetricCatalog([metric("close", "price"), metric("last", "price")])


def test_unknown_group_is_rejected_when_groups_are_given():
    with pytest.raises(CatalogError, match="group 'NOPE'"):
        MetricCatalog([metric("close", group="NOPE")], groups=["OVERVIEW"])


def test_catalog_error_is_a_value_error():
    assert issubclass(CatalogError, ValueError)
