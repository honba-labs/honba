"""`honba schema export` writes what honba-codegen (Rust) renders, for every artifact.

Rust owns schema/domain, schema/openapi, schema/mcp, the TypeScript and the .pyi outputs. The
Python command must not rebuild them from pydantic models, and must not need cargo or npx, so it
works from an installed wheel.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from honba import _honba
from honba.cli._schema_export import export_artifact, export_json_schema, generate_typescript
from honba.cli.main import app

REPO = Path(__file__).resolve().parents[3]
runner = CliRunner()


def test_the_extension_offers_every_artifact() -> None:
    assert _honba.codegen_artifacts() == ["json_schema", "openapi", "typescript", "pyi", "mcp"]


def test_an_unknown_artifact_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="yaml"):
        _honba.codegen_render("yaml")


def test_export_json_schema_writes_the_rust_bundle(tmp_path: Path) -> None:
    path = export_json_schema(tmp_path)
    assert path == tmp_path / "domain_schema.json"
    assert path.read_text() == _honba.codegen_render("json_schema")[1]


def test_typescript_is_rendered_without_npx(tmp_path: Path, monkeypatch) -> None:
    def no_subprocess(*args, **kwargs):
        raise AssertionError("TypeScript generation must not shell out")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    path = generate_typescript(tmp_path / "ts")
    assert path == tmp_path / "ts" / "domain.ts"
    text = path.read_text()
    assert "DO NOT EDIT" in text
    assert text == _honba.codegen_render("typescript")[1]


@pytest.mark.parametrize(
    ("kind", "committed"),
    [
        ("openapi", "schema/openapi/openapi.json"),
        ("pyi", "python/src/honba/wire/generated/__init__.pyi"),
        ("mcp", "schema/mcp/mcp_tools.json"),
    ],
)
def test_export_artifact_matches_the_committed_file(tmp_path: Path, kind, committed) -> None:
    path = export_artifact(kind, tmp_path)
    assert path.read_text() == (REPO / committed).read_text()


def test_cli_exports_every_requested_artifact(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "schema",
            "export",
            "--out-dir",
            str(tmp_path / "domain"),
            "--ts-out-dir",
            str(tmp_path / "ts"),
            "--openapi-dir",
            str(tmp_path / "openapi"),
            "--pyi-dir",
            str(tmp_path / "py"),
            "--mcp-dir",
            str(tmp_path / "mcp"),
        ],
    )
    assert result.exit_code == 0, result.output
    for rel in [
        "domain/domain_schema.json",
        "ts/domain.ts",
        "openapi/openapi.json",
        "py/__init__.pyi",
        "mcp/mcp_tools.json",
    ]:
        assert (tmp_path / rel).is_file(), rel
