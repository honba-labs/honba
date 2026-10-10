"""Typer CLI interface for the MCP server (E5-S1)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from honba.ai.mcp.server import main as mcp_main

app = typer.Typer(help="MCP server commands", no_args_is_help=True)


@app.command("serve")
def serve(
    data_dir: Annotated[
        Path,
        typer.Argument(help="Parquet catalog for the in-proc client"),
    ],
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="Use a running honba serve instead"),
    ] = None,
    allow_writes: Annotated[
        bool,
        typer.Option("--allow-writes", help="Turn the read-only kill switch off"),
    ] = False,
    scope: Annotated[
        str | None,
        typer.Option(
            "--scope",
            help="OAuth2 scope to filter exposed tools (read:market, read:account, write:orders, research)",
        ),
    ] = None,
) -> None:
    """Serve the Honba MCP server over stdio."""
    argv = ["--data-dir", str(data_dir)]
    if base_url is not None:
        argv += ["--base-url", base_url]
    if allow_writes:
        argv += ["--allow-writes"]
    if scope is not None:
        argv += ["--scope", scope]
    raise SystemExit(mcp_main(argv))
