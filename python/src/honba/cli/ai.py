"""Typer CLI interface for AI and knowledge pack commands (Design.md Section 13.5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from honba.ai.knowledge import build_knowledge_pack

app = typer.Typer(help="AI and knowledge pack commands", no_args_is_help=True)
knowledge_app = typer.Typer(help="Manage knowledge pack artifacts", no_args_is_help=True)
app.add_typer(knowledge_app, name="knowledge")

console = Console()
err_console = Console(stderr=True)


@knowledge_app.command("export")
def export_knowledge(
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Target JSON file path")] = None,
) -> None:
    """Export the current generated knowledge pack as JSON."""
    pack = build_knowledge_pack()
    dumped = pack.to_json()
    if out:
        out.write_text(dumped, encoding="utf-8")
        console.print(f"[green]Exported knowledge pack to[/green] {out} (hash: {pack.content_hash})")
    else:
        typer.echo(dumped)


@knowledge_app.command("check")
def check_knowledge(
    reference: Annotated[Path, typer.Option("--reference", "-r", help="Reference JSON file to verify against")],
) -> None:
    """Verify that current knowledge pack matches a reference file (fails CI on drift)."""
    if not reference.exists():
        err_console.print(f"[red]Reference file does not exist:[/red] {reference}")
        raise typer.Exit(code=1)

    ref_data = json.loads(reference.read_text(encoding="utf-8"))
    ref_hash = ref_data.get("content_hash")

    pack = build_knowledge_pack()
    if pack.content_hash != ref_hash:
        err_console.print(
            f"[red]Knowledge pack drift detected![/red]\n"
            f"Reference hash: {ref_hash}\n"
            f"Generated hash: {pack.content_hash}\n"
            f"Run 'honba ai knowledge export --out <file>' to refresh."
        )
        raise typer.Exit(code=1)

    console.print(f"[green]Knowledge pack is up-to-date (hash: {pack.content_hash})[/green]")
