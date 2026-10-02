"""Unit/integration tests for the screener CLI commands (Design.md Section 3).

Tests written first (TDD, red -> green).
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from honba.cli.main import app

runner = CliRunner()


def test_screener_metrics_list():
    result = runner.invoke(app, ["screener", "metrics", "list"])
    assert result.exit_code == 0
    assert "close" in result.output
    assert "market_cap_basic" in result.output
    assert "RSI" in result.output


def test_screener_metrics_show():
    result = runner.invoke(app, ["screener", "metrics", "show", "market cap"])
    assert result.exit_code == 0
    assert "market_cap_basic" in result.output
    assert "OVERVIEW" in result.output


def test_screener_presets_list():
    result = runner.invoke(app, ["screener", "presets", "list"])
    assert result.exit_code == 0
    assert "52_week_low" in result.output
    assert "52_week_high" in result.output


def test_screener_presets_show():
    result = runner.invoke(app, ["screener", "presets", "show", "52_week_low"])
    assert result.exit_code == 0
    assert "52 Week Low" in result.output
    assert "price_52_week_low" in result.output


def test_screener_explain_outputs_resolved_request_json():
    result = runner.invoke(
        app,
        [
            "screener",
            "explain",
            "--market",
            "india",
            "market",
            "cap",
            "above",
            "10000",
            "Cr",
        ],
    )
    assert result.exit_code == 0
    # output should contain resolved request JSON
    assert "market_cap_basic" in result.output
    assert "100000000000" in result.output


def test_screener_scan_print_request():
    result = runner.invoke(
        app,
        [
            "screener",
            "scan",
            "--market",
            "india",
            "--print-request",
            "rsi",
            "below",
            "30",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["market"] == "india"
    assert data["filters"]["items"][0]["key"] == "RSI"
    assert data["filters"]["items"][0]["op"] == "lt"
    assert data["filters"]["items"][0]["value"] == 30.0


def test_screener_invalid_filter_exits_with_code_1():
    result = runner.invoke(
        app,
        [
            "screener",
            "explain",
            "rsi",
            "below",
            "30",
            "Cr",
        ],
    )
    assert result.exit_code == 1
    assert "^" in result.output or "multiplier" in result.output or "unit" in result.output


def test_screener_scan_table_and_json():
    res_table = runner.invoke(app, ["screener", "scan", "--market", "india", "close", "above", "100"])
    assert res_table.exit_code == 0
    assert "Screener Results" in res_table.output

    res_json = runner.invoke(app, ["screener", "scan", "--market", "india", "--format", "json", "close", "above", "100"])
    assert res_json.exit_code == 0
    doc = json.loads(res_json.output)
    assert "total" in doc
    assert "rows" in doc

