"""Honba schema CLI commands (E0-S2/E0-S4, Pillar P2).

Every artifact is rendered by honba-codegen (Rust) through the compiled extension; see
`honba.cli._schema_export`.
"""

from __future__ import annotations

from pathlib import Path

import typer

from honba.cli._schema_export import export_artifact, export_json_schema, generate_typescript

app = typer.Typer(
    help="Export canonical schemas and generate client contracts", no_args_is_help=True
)


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
        "-t/-T",
        help="Also write generated TypeScript definitions for honba-frontend",
    ),
    ts_out_dir: str = typer.Option(
        "../honba-frontend/src/core/types/generated",
        "--ts-out-dir",
        help="Target directory for generated TypeScript definitions",
    ),
    openapi_dir: str | None = typer.Option(
        None, "--openapi-dir", help="Also write openapi.json into this directory"
    ),
    pyi_dir: str | None = typer.Option(
        None, "--pyi-dir", help="Also write the Python wire stubs (__init__.pyi) here"
    ),
    mcp_dir: str | None = typer.Option(
        None, "--mcp-dir", help="Also write mcp_tools.json into this directory"
    ),
) -> None:
    """Export the domain JSON Schema and, optionally, the derived artifacts."""
    base_dir = Path.cwd()
    schema_file = export_json_schema(base_dir / out_dir)
    typer.echo(f"Exported JSON Schema to {schema_file}")

    if generate_ts:
        ts_file = generate_typescript(base_dir / ts_out_dir)
        typer.echo(f"Generated TypeScript definitions to {ts_file}")

    for kind, target in (("openapi", openapi_dir), ("pyi", pyi_dir), ("mcp", mcp_dir)):
        if target is not None:
            path = export_artifact(kind, base_dir / target)
            typer.echo(f"Exported {kind} to {path}")
