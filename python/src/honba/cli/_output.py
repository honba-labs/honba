"""Shared ``--format`` handling for CLI commands that print rows."""

from __future__ import annotations

import typer

from honba.display import OutputFormat

FORMAT_HELP = "Output format: table, json, csv, plain"


def parse_format(value: str) -> OutputFormat:
    """Parse a ``--format`` value, raising a usage error (exit code 2) when unknown."""
    try:
        return OutputFormat.parse(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--format") from exc
