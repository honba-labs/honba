"""`honba schema export` works from the installed package and matches the committed bundle."""

import importlib
from pathlib import Path

from typer.testing import CliRunner

import honba
from honba.cli.main import app

COMMITTED = Path(__file__).resolve().parents[3] / "schema" / "domain" / "domain_schema.json"
runner = CliRunner()


def test_schema_command_imports_from_the_package_not_the_repo_scripts():
    mod = importlib.import_module("honba.cli.schema")
    assert hasattr(mod, "export_json_schema") and hasattr(mod, "generate_typescript")
    export_mod = importlib.import_module(mod.export_json_schema.__module__)
    package_dir = Path(honba.__file__).resolve().parent
    assert package_dir in Path(export_mod.__file__).resolve().parents


def test_schema_export_produces_the_committed_bundle(tmp_path):
    result = runner.invoke(app, ["schema", "export", "--no-ts", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    produced = tmp_path / "domain_schema.json"
    assert produced.read_text() == COMMITTED.read_text()
