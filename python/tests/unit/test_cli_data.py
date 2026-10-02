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
    assert "No covered intervals found" in res.output


def test_data_cli_gaps():
    res = runner.invoke(app, ["data", "gaps", "RELIANCE", "--start", "2024-01-01", "--end", "2024-06-01"])
    assert res.exit_code == 0
    assert "Missing Gaps for" in res.output
    assert "2024-01-01" in res.output
    assert "2024-06-01" in res.output
