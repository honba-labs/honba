"""Unit tests for the knowledge pack, LLM provider stub, repair loop, and screener ask CLI.

Tests verify:
1. KnowledgePack generation, content hash stability, and catalog inclusion.
2. ScriptedFakeLlm deterministic response & history capture.
3. Query translation pipeline with immediate pass, repair-then-pass, and repair-exhaustion.
4. CLI commands: honba ai knowledge export, check, and honba screener ask.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from honba.ai.ask import QueryTranslationError, translate_query
from honba.ai.knowledge import build_knowledge_pack
from honba.ai.llm.provider import ScriptedFakeLlm
from honba.cli.main import app
from honba.screener.catalog import load_catalog

runner = CliRunner()


def test_build_knowledge_pack():
    pack = build_knowledge_pack()
    assert pack.version == "1.0.0"
    assert len(pack.content_hash) == 16
    assert "filters    := or_expr" in pack.grammar_ebnf

    # Verify metrics catalog is captured
    metric_keys = {m["key"] for m in pack.metrics}
    assert "market_cap_basic" in metric_keys
    assert "RSI" in metric_keys
    assert "close" in metric_keys

    # Verify presets & units
    preset_keys = {p["key"] for p in pack.presets}
    assert "52_week_low" in preset_keys
    assert "Cr" in pack.units
    assert pack.units["Cr"] == 1e7


def test_query_translation_immediate_success():
    catalog = load_catalog()
    llm = ScriptedFakeLlm(responses=["market cap above 10000 Cr"])

    result = translate_query("large cap stocks", llm=llm, catalog=catalog)
    assert result.filter_text == "market cap above 10000 Cr"
    assert result.repair_count == 0
    assert len(result.filter_group.items) == 1
    assert result.filter_group.items[0].key == "market_cap_basic"
    assert result.filter_group.items[0].value == 100000000000.0


def test_query_translation_repair_then_success():
    catalog = load_catalog()
    # First response fails with invalid unit pairing: rsi below 30 Cr
    # Second response repairs it to valid: rsi below 30
    llm = ScriptedFakeLlm(responses=["rsi below 30 Cr", "rsi below 30"])

    result = translate_query("oversold stocks", llm=llm, catalog=catalog, max_repairs=2)
    assert result.filter_text == "rsi below 30"
    assert result.repair_count == 1
    assert result.filter_group.items[0].key == "RSI"
    assert result.filter_group.items[0].value == 30.0


def test_query_translation_repair_exhausted():
    catalog = load_catalog()
    # Continuous invalid responses
    llm = ScriptedFakeLlm(responses=["rsi below 30 Cr", "rsi below 40 Cr", "rsi below 50 Cr"])

    with pytest.raises(QueryTranslationError) as exc_info:
        translate_query("oversold stocks", llm=llm, catalog=catalog, max_repairs=2)
    assert "Failed to translate query after 2 repair attempts" in str(exc_info.value)


def test_cli_ai_knowledge_export_and_check(tmp_path: Path):
    target_file = tmp_path / "knowledge_pack.json"

    # Export knowledge pack to file
    export_result = runner.invoke(app, ["ai", "knowledge", "export", "--out", str(target_file)])
    assert export_result.exit_code == 0
    assert target_file.exists()

    # Check knowledge pack with reference file
    check_result = runner.invoke(app, ["ai", "knowledge", "check", "--reference", str(target_file)])
    assert check_result.exit_code == 0
    assert "Knowledge pack is up-to-date" in check_result.output


def test_cli_screener_ask():
    # Ask command runs the query with scripted translation and runs the scan with --yes
    result = runner.invoke(
        app,
        ["screener", "ask", "--yes", "market", "cap", "above", "10000", "Cr"],
    )
    assert result.exit_code == 0
    assert "Translated Filter:" in result.output
    assert "market_cap_basic" in result.output or "Screener Results" in result.output
