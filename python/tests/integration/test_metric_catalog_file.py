"""Integration tests: the real ``schema/catalog/metric_catalog.json`` through the loader.

File -> ``load_catalog`` -> ``MetricDefinition`` validation -> ``MetricCatalog`` build
and resolution. No network; the only I/O is reading the committed catalog file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from honba.entities.screener import (
    MetricDefinition,
    MetricPeriod,
    MetricRef,
    ScreenerFilterPredicate,
    Timeframe,
)
from honba.screener import (
    CATALOG_ENV_VAR,
    AmbiguousMetric,
    CatalogError,
    default_catalog_path,
    load_catalog,
)

CATALOG_FILE = Path(__file__).resolve().parents[3] / "schema" / "catalog" / "metric_catalog.json"
SECURITY_KEYS = {"name", "description", "exchange", "country", "sector"}


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads(CATALOG_FILE.read_text())


@pytest.fixture(scope="module")
def entries(doc) -> list[MetricDefinition]:
    # Strict validation, no stripping: every entry must be a valid MetricDefinition.
    return [MetricDefinition.model_validate(e) for e in doc["metrics"]]


def test_default_path_is_the_repo_catalog(monkeypatch):
    monkeypatch.delenv(CATALOG_ENV_VAR, raising=False)
    assert default_catalog_path() == CATALOG_FILE


def test_header(doc):
    assert doc["version"] == 1
    assert doc["notes"]
    assert len(doc["groups"]) == len(set(doc["groups"]))


def test_every_used_group_is_declared(doc, entries):
    assert {m.group for m in entries} <= set(doc["groups"])


def test_security_metrics_are_present(entries):
    by_key = {m.key: m for m in entries}
    assert SECURITY_KEYS <= set(by_key)
    assert {by_key[k].group for k in SECURITY_KEYS} == {"SECURITY"}
    assert all(by_key[k].filterable for k in SECURITY_KEYS)
    assert by_key["name"].ui_id == "symbol"
    assert by_key["description"].ui_id == "name"


def test_sma50_is_a_timeframed_technical(entries):
    sma50 = next(m for m in entries if m.key == "SMA50")
    assert (sma50.ui_id, sma50.group, sma50.has_timeframe) == ("sma50", "TECHNICALS", True)


def test_every_metric_has_ui_id_and_one_to_four_aliases(entries):
    for m in entries:
        assert m.ui_id, m.key
        assert 1 <= len(m.aliases) <= 4, m.key


def test_ui_ids_and_keys_are_unique(entries):
    ui_ids = [m.ui_id for m in entries]
    keys = [m.key for m in entries]
    assert len(ui_ids) == len(set(ui_ids))
    assert len(keys) == len(set(keys))


def test_default_dimensions_are_valid_wire_values(entries):
    # MetricDefinition keeps defaultPeriod / defaultTimeframe as plain strings; the catalog
    # must still only use canonical MetricPeriod / Timeframe values.
    for m in entries:
        if m.default_period is not None:
            assert m.has_period, m.key
            MetricPeriod(m.default_period)
        if m.default_timeframe is not None:
            assert m.has_timeframe, m.key
            Timeframe(m.default_timeframe)


def test_loader_builds_a_consistent_catalog(entries):
    catalog = load_catalog()
    assert len(catalog) == len(entries)
    assert catalog.groups is not None and "SECURITY" in catalog.groups


def test_bare_change_is_ambiguous_in_the_real_catalog():
    with pytest.raises(AmbiguousMetric) as info:
        load_catalog().resolve("change")
    assert set(info.value.candidates) == {"change", "change_abs"}


@pytest.mark.parametrize(
    ("phrase", "key"),
    [
        ("change %", "change"),
        ("change abs", "change_abs"),
        ("52w-low", "price_52_week_low"),
        ("52 week low", "price_52_week_low"),
        ("52W LOW", "price_52_week_low"),
        ("rsi", "RSI"),
        ("sma200", "SMA200"),
        ("market cap", "market_cap_basic"),
        ("P/E", "price_earnings_ttm"),
        ("roce", "return_on_capital_employed"),
        ("delivery %", "delivery_pct"),
        ("ticker", "name"),
        ("company name", "description"),
    ],
)
def test_phrases_resolve_to_wire_keys(phrase, key):
    assert load_catalog().resolve(phrase).key == key


def test_resolved_metrics_build_a_metric_to_metric_predicate():
    catalog = load_catalog()
    left = catalog.resolve("50 day moving average")
    right = catalog.resolve("200 dma")
    pred = ScreenerFilterPredicate.model_validate(
        {"key": left.key, "op": "crosses_above", "value": {"key": right.key}, "timeframe": "1D"}
    )
    assert pred.value == MetricRef(key="SMA200")
    assert pred.model_dump(mode="json", by_alias=True) == {
        "key": "SMA50",
        "op": "crosses_above",
        "value": {"key": "SMA200"},
        "timeframe": "1D",
    }


def test_explicit_path_and_env_override(tmp_path, monkeypatch, doc):
    small = {**doc, "metrics": [e for e in doc["metrics"] if e["key"] in SECURITY_KEYS]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(small))
    assert len(load_catalog(path)) == 5
    monkeypatch.setenv(CATALOG_ENV_VAR, str(path))
    assert len(load_catalog()) == 5


def test_missing_file_names_the_path_and_override(tmp_path):
    with pytest.raises(FileNotFoundError, match=CATALOG_ENV_VAR):
        load_catalog(tmp_path / "nope.json")


def test_inconsistent_file_is_rejected(tmp_path, doc):
    bad = json.loads(json.dumps(doc))
    bad["metrics"][1]["aliases"].append(bad["metrics"][0]["key"])
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(CatalogError):
        load_catalog(path)
