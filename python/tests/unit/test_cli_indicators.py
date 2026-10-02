"""`honba indicators ...`: discovery for humans (tables) and agents (--json)."""

import json

from typer.testing import CliRunner

from honba.cli.main import app

runner = CliRunner()


def test_list_groups_by_family():
    r = runner.invoke(app, ["indicators", "list"])
    assert r.exit_code == 0
    out = r.stdout
    for family in ("moving_average", "trend", "momentum", "volatility", "volume"):
        assert family in out
    assert "rsi" in out and "supertrend" in out
    assert out.index("moving_average") < out.index("volatility")


def test_list_filtered_by_family():
    r = runner.invoke(app, ["indicators", "list", "--family", "volatility"])
    assert r.exit_code == 0
    assert "atr" in r.stdout and "rsi" not in r.stdout


def test_list_json_is_machine_readable():
    r = runner.invoke(app, ["indicators", "list", "--json"])
    data = json.loads(r.stdout)
    assert {"kind", "family", "inputs", "outputs", "params", "summary"} <= set(data[0])
    assert len(data) >= 74


def test_show_prints_params_and_json():
    r = runner.invoke(app, ["indicators", "show", "rsi"])
    assert r.exit_code == 0 and "period" in r.stdout and "14" in r.stdout
    j = json.loads(runner.invoke(app, ["indicators", "show", "supertrend", "--json"]).stdout)
    assert j["kind"] == "supertrend" and j["outputs"] == ["value", "direction"]


def test_unknown_kind_and_family_fail_cleanly():
    r = runner.invoke(app, ["indicators", "show", "nope"])
    assert r.exit_code == 1 and "unknown indicator" in (r.stdout + r.stderr)
    r = runner.invoke(app, ["indicators", "list", "--family", "astrology"])
    assert r.exit_code == 1 and "unknown family" in (r.stdout + r.stderr)
