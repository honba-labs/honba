from typer.testing import CliRunner

from honba.cli.main import app

runner = CliRunner()


def test_data_cli_help():
    res = runner.invoke(app, ["data", "--help"])
    assert res.exit_code == 0
    assert "coverage" in res.output
    assert "gaps" in res.output
    assert "fetch" in res.output


def test_data_cli_coverage():
    res = runner.invoke(app, ["data", "coverage"])
    assert res.exit_code == 0
    assert "Data Store Coverage" in res.output or "No covered intervals found" in res.output


def test_data_cli_gaps(tmp_path, monkeypatch):
    # Hermetic: the CLI's module-level store reads the developer's (gitignored)
    # data/coverage_ledger.json, so a local RELIANCE download hid every gap.
    from honba.cli import data as data_cli

    monkeypatch.setattr(data_cli._STORE, "ledger_file", tmp_path / "coverage_ledger.json")
    res = runner.invoke(
        app, ["data", "gaps", "RELIANCE", "--start", "2024-01-01", "--end", "2024-06-01"]
    )
    assert res.exit_code == 0
    assert "Missing Gaps for" in res.output
    assert "2024-01-01" in res.output
    assert "2024-06-01" in res.output
