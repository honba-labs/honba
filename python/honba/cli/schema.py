"""Honba schema CLI commands (E0-S2/E0-S4, Pillar P2)."""

from __future__ import annotations

from pathlib import Path

import typer

from honba.cli._schema_export import export_json_schema, generate_typescript

app = typer.Typer(help="Export canonical schemas and generate client contracts")


@app.command("export")
def export_command(
    out_dir: str = typer.Option(
        "schema/domain",
        "--out-dir",
        "-o",
        help="Target directory for domain JSON schemas",
    ),
    generate_ts: bool = typer.Option(
        True,
        "--ts/--no-ts",
        help="Also compile generated TypeScript definitions for honba-frontend",
    ),
    ts_out_dir: str = typer.Option(
        "../honba-frontend/src/core/types/generated",
        "--ts-out-dir",
        help="Target directory for generated TypeScript definitions",
    ),
) -> None:
    """Export domain JSON Schema and compile TypeScript contracts."""
    base_dir = Path.cwd()
    schema_path = base_dir / out_dir
    schema_file = export_json_schema(schema_path)
    typer.echo(f"Exported JSON Schema to {schema_file}")

    if generate_ts:
        ts_path = base_dir / ts_out_dir
        generate_typescript(schema_file, ts_path)
        typer.echo(f"Compiled TypeScript definitions to {ts_path / 'domain.ts'}")
