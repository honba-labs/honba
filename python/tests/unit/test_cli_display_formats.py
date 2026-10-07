"""CLI commands that print rows share ``honba.display`` and accept ``--format``."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

import pytest
from typer.testing import CliRunner

from honba.cli import data as data_cli
from honba.cli.main import app
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")


def _csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_metrics_list_json_has_stable_keys() -> None:
    res = runner.invoke(app, ["screener", "metrics", "list", "--format", "json"])
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert list(rows[0]) == ["key", "group", "type", "unit", "aliases"]
    assert any(r["key"] == "market_cap_basic" for r in rows)


def test_metrics_list_csv_header() -> None:
    res = runner.invoke(app, ["screener", "metrics", "list", "--format", "csv"])
    assert res.exit_code == 0
    assert res.output.splitlines()[0] == "key,group,type,unit,aliases"


def test_metrics_list_table_is_the_default_and_keeps_its_title() -> None:
    res = runner.invoke(app, ["screener", "metrics", "list"])
    assert "Screener Metric Catalog" in res.output
    assert "\x1b[" not in res.output


def test_presets_list_json_and_csv() -> None:
    res = runner.invoke(app, ["screener", "presets", "list", "--format", "json"])
    assert any(r["key"] == "52_week_low" for r in json.loads(res.output))
    res = runner.invoke(app, ["screener", "presets", "list", "-f", "csv"])
    assert res.output.splitlines()[0] == "key,label,target_metric,aliases"


def test_scan_csv_has_symbol_name_and_metric_columns() -> None:
    res = runner.invoke(
        app, ["screener", "scan", "--market", "india", "--format", "csv", "close", "above", "100"]
    )
    assert res.exit_code == 0
    header = res.output.splitlines()[0].split(",")
    assert header[:2] == ["symbol", "name"]


def test_unknown_format_is_a_usage_error() -> None:
    res = runner.invoke(app, ["screener", "metrics", "list", "--format", "xml"])
    assert res.exit_code != 0
    assert "format" in res.output.lower()


def test_metrics_show_renders_key_value_rows() -> None:
    res = runner.invoke(app, ["screener", "metrics", "show", "market cap"])
    assert res.exit_code == 0
    assert "Key" in res.output and "market_cap_basic" in res.output


@pytest.fixture
def _coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = CoverageRecord(
        exchange="NSE",
        symbol="RELIANCE",
        timeframe="1D",
        interval=DateInterval(dt.date(2024, 1, 1), dt.date(2024, 3, 1)),
        status=CoverageStatus.FINAL,
        source="test",
        row_count=42,
    )
    monkeypatch.setattr(data_cli._STORE, "coverage", lambda inst, tf: [rec])


@pytest.mark.usefixtures("_coverage")
def test_data_coverage_formats() -> None:
    table = runner.invoke(app, ["data", "coverage", "RELIANCE"])
    assert "Data Store Coverage" in table.output and "RELIANCE" in table.output
    rows = json.loads(runner.invoke(app, ["data", "coverage", "RELIANCE", "-f", "json"]).output)
    assert rows[0]["symbol"] == "RELIANCE" and rows[0]["rows"] == 42
    assert list(rows[0]) == ["exchange", "symbol", "timeframe", "interval", "status", "rows"]
    parsed = _csv(runner.invoke(app, ["data", "coverage", "RELIANCE", "-f", "csv"]).output)
    assert parsed[0]["rows"] == "42"


def test_data_gaps_json_and_csv(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_cli._STORE, "ledger_file", tmp_path / "coverage_ledger.json")
    args = ["data", "gaps", "RELIANCE", "--start", "2024-01-01", "--end", "2024-06-01"]
    rows = json.loads(runner.invoke(app, [*args, "--format", "json"]).output)
    assert list(rows[0]) == ["gap_start", "gap_end"]
    assert rows[0]["gap_start"] == "2024-01-01"
    parsed = _csv(runner.invoke(app, [*args, "--format", "csv"]).output)
    assert parsed[0]["gap_end"] == "2024-06-01"
