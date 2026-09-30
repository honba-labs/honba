"""``honba indicators``: discover indicators and their parameters."""
from __future__ import annotations

import json

import typer

from honba.strategies.indicators import FAMILIES, indicator_spec, list_indicators

app = typer.Typer(help="Discover the built-in technical indicators.", no_args_is_help=True)


def _fail(msg: str) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(1)


@app.command("list")
def list_cmd(
    family: str = typer.Option(None, "--family", "-f", help=f"One of: {', '.join(FAMILIES)}"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List indicators, grouped by family."""
    try:
        kinds = list_indicators(family)
    except ValueError as e:
        _fail(str(e))
    specs = [indicator_spec(k) for k in kinds]
    if as_json:
        typer.echo(json.dumps(specs, indent=2))
        return
    for fam in FAMILIES:
        rows = [s for s in specs if s["family"] == fam]
        if not rows:
            continue
        typer.echo(f"{fam}:")
        for s in rows:
            typer.echo(f"  {s['kind']:<24}{s['summary']}")


@app.command()
def show(
    kind: str = typer.Argument(..., help="Indicator kind, e.g. rsi"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show an indicator's family, inputs, outputs and parameters (with defaults)."""
    try:
        spec = indicator_spec(kind)
    except ValueError as e:
        _fail(str(e))
    if as_json:
        typer.echo(json.dumps(spec, indent=2))
        return
    typer.echo(f"{spec['kind']} ({spec['family']}): {spec['summary']}")
    typer.echo(f"  inputs:  {', '.join(spec['inputs'])}")
    typer.echo(f"  outputs: {', '.join(spec['outputs'])}")
    typer.echo("  params:")
    for p in spec["params"]:
        default = f" = {p['default']!r}" if "default" in p else ""
        typer.echo(f"    {p['name']}{default}  ({p.get('type', 'any')})")
