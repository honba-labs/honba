"""Test the CLI `honba mcp serve` command."""

from __future__ import annotations

from typer.testing import CliRunner

from honba.cli.main import app

runner = CliRunner()


def test_mcp_serve_help():
    res = runner.invoke(app, ["mcp", "serve", "--help"])
    assert res.exit_code == 0
    assert "Serve the Honba MCP server over stdio" in res.output
    assert "--base-url" in res.output
    assert "--allow-writes" in res.output


def test_mcp_serve_missing_data_dir():
    res = runner.invoke(app, ["mcp", "serve"])
    assert res.exit_code != 0
    assert "data_dir" in res.output.lower()


def test_mcp_serve_invalid_data_dir():
    res = runner.invoke(app, ["mcp", "serve", "/does/not/exist"])
    assert res.exit_code != 0
    # The actual validation happens in the MCP server, so it might pass CLI but fail later
    # Just ensure it doesn't crash on the CLI level
