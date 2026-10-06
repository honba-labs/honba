"""``honba verify``: compile a strategy manifest and print its IR as JSON (plan.md E0-S8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from honba.strategies.verify import VerifyError, verify_manifest


def verify(
    manifest: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Manifest JSON file")
    ],
) -> None:
    """Verify a strategy manifest and print its compiled IR as JSON."""
    try:
        ir = verify_manifest(manifest.read_text())
    except VerifyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(ir, indent=2))
