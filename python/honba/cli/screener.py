"""Typer CLI interface for the Screener app (Design.md Section 3)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from honba.entities.screener import (
    MetricKeySpec,
    ScreenerFilterGroup,
    ScreenerScanRequest,
    ScreenerSortSpec,
    Timeframe,
)
from honba.query.parser import FilterParseError, parse_filters
from honba.screener.catalog import MetricCatalog, MetricResolutionError, load_catalog
from honba.screener.presets import list_presets, resolve_preset_key

app = typer.Typer(help="Screener commands: scan, explain, metrics, presets")
metrics_app = typer.Typer(help="Browse the metric catalog")
presets_app = typer.Typer(help="Browse named criteria presets")

app.add_typer(metrics_app, name="metrics")
app.add_typer(presets_app, name="presets")

console = Console()
err_console = Console(stderr=True)


@metrics_app.command("list")
def list_metrics() -> None:
    """List all available metrics in the catalog."""
    catalog = load_catalog()
    table = Table(title="Screener Metric Catalog")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Group", style="magenta")
    table.add_column("Type", style="green")
    table.add_column("Unit", style="yellow")
    table.add_column("Aliases", style="white")

    for m in catalog:
        table.add_row(
            m.key,
            m.group,
            m.value_type.value,
            m.unit.value if m.unit else "-",
            ", ".join(m.aliases),
        )
    console.print(table)


@metrics_app.command("show")
def show_metric(phrase: str) -> None:
    """Show details for a metric key or alias."""
    catalog = load_catalog()
    try:
        m = catalog.resolve(phrase)
    except MetricResolutionError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    table = Table(title=f"Metric: {m.key}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Key", m.key)
    table.add_row("UI Id", m.ui_id or "-")
    table.add_row("Label", m.label)
    table.add_row("Group", m.group)
    table.add_row("Value Type", m.value_type.value)
    table.add_row("Unit", m.unit.value if m.unit else "-")
    table.add_row("Has Timeframe", str(m.has_timeframe))
    table.add_row("Has Period", str(m.has_period))
    table.add_row("Aliases", ", ".join(m.aliases))
    if m.description:
        table.add_row("Description", m.description)

    console.print(table)


@presets_app.command("list")
def list_presets_cmd() -> None:
    """List all named screener criteria presets."""
    presets = list_presets()
    table = Table(title="Screener Presets")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Label", style="green")
    table.add_column("Target Metric", style="yellow")
    table.add_column("Aliases", style="white")

    for p in presets.values():
        table.add_row(p.key, p.label, p.target_metric, ", ".join(p.aliases))
    console.print(table)


@presets_app.command("show")
def show_preset_cmd(name: str) -> None:
    """Show details for a preset."""
    p = resolve_preset_key(name)
    if p is None:
        err_console.print(f"[red]Error:[/red] unknown preset {name!r}")
        raise typer.Exit(code=1)

    table = Table(title=f"Preset: {p.key}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Key", p.key)
    table.add_row("Label", p.label)
    table.add_row("Target Metric", p.target_metric)
    table.add_row("Lookback Bars", str(p.lookback_bars))
    table.add_row("Aliases", ", ".join(p.aliases))
    table.add_row("Description", p.description)

    console.print(table)


def _build_scan_request(
    market: str,
    types: list[str],
    primary_only: bool,
    timescale: str | None,
    period: str | None,
    columns: str | None,
    column_set: str | None,
    sort: str | None,
    limit: int,
    offset: int,
    filter_words: list[str],
    catalog: MetricCatalog,
) -> ScreenerScanRequest:
    filter_group: ScreenerFilterGroup | None = None
    if filter_words:
        filter_text = " ".join(filter_words)
        try:
            filter_group = parse_filters(filter_text, catalog=catalog, market=market)
        except FilterParseError as exc:
            err_console.print(f"[red]Parse error:[/red] {exc}")
            raise typer.Exit(code=1)
        except ValueError as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=1)

    # Columns
    col_specs: list[MetricKeySpec] = []
    if columns:
        for c in columns.split(","):
            c = c.strip()
            if not c:
                continue
            if "@" in c:
                k, tf = c.split("@", 1)
                col_specs.append(MetricKeySpec(key=k, timeframe=Timeframe(tf)))
            else:
                col_specs.append(MetricKeySpec(key=c))

    # Sort
    sort_spec: ScreenerSortSpec | None = None
    if sort:
        parts = sort.split(":")
        k_tf = parts[0]
        direction = parts[1] if len(parts) > 1 else "desc"
        if "@" in k_tf:
            k, tf = k_tf.split("@", 1)
            sort_spec = ScreenerSortSpec(key=k, dir=direction, timeframe=Timeframe(tf))
        else:
            sort_spec = ScreenerSortSpec(key=k_tf, dir=direction)

    return ScreenerScanRequest(
        market=market,
        types=types,
        primary_only=primary_only,
        column_set=column_set,
        columns=col_specs,
        filters=filter_group,
        sort=sort_spec,
        range=(offset, limit),
    )


from typing import Annotated


@app.command("explain")
def explain_cmd(
    market: Annotated[str, typer.Option("--market", help="Market identifier")] = "india",
    types: Annotated[list[str] | None, typer.Option("--type", help="Asset type(s)")] = None,
    all_listings: Annotated[
        bool, typer.Option("--all-listings", help="Include non-primary listings")
    ] = False,
    timescale: Annotated[
        str | None, typer.Option("--timescale", "-t", help="Default timeframe")
    ] = None,
    period: Annotated[str | None, typer.Option("--period", help="Default period")] = None,
    columns: Annotated[
        str | None, typer.Option("--columns", help="Comma-separated metric keys")
    ] = None,
    column_set: Annotated[str | None, typer.Option("--column-set", help="Named column set")] = None,
    sort: Annotated[str | None, typer.Option("--sort", help="Sort spec, e.g. close:desc")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max results")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="Offset")] = 0,
    filters: Annotated[
        list[str] | None, typer.Argument(help="Trailing filter words")
    ] = None,
) -> None:
    """Parse and validate; print the parse tree and resolved request without running."""
    catalog = load_catalog()
    req = _build_scan_request(
        market=market,
        types=types or ["EQUITY"],
        primary_only=not all_listings,
        timescale=timescale,
        period=period,
        columns=columns,
        column_set=column_set,
        sort=sort,
        limit=limit,
        offset=offset,
        filter_words=filters or [],
        catalog=catalog,
    )
    dumped = req.model_dump(mode="json", by_alias=True)
    console.print_json(data=dumped)


@app.command("scan")
def scan_cmd(
    market: Annotated[str, typer.Option("--market", help="Market identifier")] = "india",
    types: Annotated[list[str] | None, typer.Option("--type", help="Asset type(s)")] = None,
    all_listings: Annotated[
        bool, typer.Option("--all-listings", help="Include non-primary listings")
    ] = False,
    timescale: Annotated[
        str | None, typer.Option("--timescale", "-t", help="Default timeframe")
    ] = None,
    period: Annotated[str | None, typer.Option("--period", help="Default period")] = None,
    columns: Annotated[
        str | None, typer.Option("--columns", help="Comma-separated metric keys")
    ] = None,
    column_set: Annotated[str | None, typer.Option("--column-set", help="Named column set")] = None,
    sort: Annotated[str | None, typer.Option("--sort", help="Sort spec, e.g. close:desc")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max results")] = 50,
    offset: Annotated[int, typer.Option("--offset", help="Offset")] = 0,
    print_request: Annotated[
        bool, typer.Option("--print-request", help="Emit resolved request JSON and exit")
    ] = False,
    filters: Annotated[
        list[str] | None, typer.Argument(help="Trailing filter words")
    ] = None,
) -> None:
    """Run a scan and render results (or print the request JSON)."""
    catalog = load_catalog()
    req = _build_scan_request(
        market=market,
        types=types or ["EQUITY"],
        primary_only=not all_listings,
        timescale=timescale,
        period=period,
        columns=columns,
        column_set=column_set,
        sort=sort,
        limit=limit,
        offset=offset,
        filter_words=filters or [],
        catalog=catalog,
    )
    if print_request:
        typer.echo(req.model_dump_json(by_alias=True, indent=2))
        return

    # Execution against ScreenerSource will follow in Step 4
    console.print(f"[yellow]Scan execution for {market} will execute via ScreenerSource[/yellow]")
