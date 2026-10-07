"""Typer CLI interface for the Screener app (Design.md Section 3)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from honba.cli._output import FORMAT_HELP, parse_format
from honba.display import Column, render, render_kv
from honba.entities.instrument import InstrumentId
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

app = typer.Typer(help="Screener commands: scan, explain, metrics, presets", no_args_is_help=True)
metrics_app = typer.Typer(help="Browse the metric catalog", no_args_is_help=True)
presets_app = typer.Typer(help="Browse named criteria presets", no_args_is_help=True)

app.add_typer(metrics_app, name="metrics")
app.add_typer(presets_app, name="presets")

console = Console()
err_console = Console(stderr=True)


@metrics_app.command("list")
def list_metrics(
    format: Annotated[str, typer.Option("--format", "-f", help=FORMAT_HELP)] = "table",
) -> None:
    """List all available metrics in the catalog."""
    fmt = parse_format(format)
    catalog = load_catalog()
    rows = [
        {
            "key": m.key,
            "group": m.group,
            "type": m.value_type.value,
            "unit": m.unit.value if m.unit else "-",
            "aliases": ", ".join(m.aliases),
        }
        for m in catalog
    ]
    render(
        rows,
        [
            Column("key", "Key", style="cyan"),
            Column("group", "Group", style="magenta"),
            Column("type", "Type", style="green"),
            Column("unit", "Unit", style="yellow"),
            Column("aliases", "Aliases"),
        ],
        fmt,
        title="Screener Metric Catalog",
    )


@metrics_app.command("show")
def show_metric(phrase: str) -> None:
    """Show details for a metric key or alias."""
    catalog = load_catalog()
    try:
        m = catalog.resolve(phrase)
    except MetricResolutionError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    pairs = [
        ("Key", m.key),
        ("UI Id", m.ui_id or "-"),
        ("Label", m.label),
        ("Group", m.group),
        ("Value Type", m.value_type.value),
        ("Unit", m.unit.value if m.unit else "-"),
        ("Has Timeframe", str(m.has_timeframe)),
        ("Has Period", str(m.has_period)),
        ("Aliases", ", ".join(m.aliases)),
    ]
    if m.description:
        pairs.append(("Description", m.description))
    render_kv(pairs, title=f"Metric: {m.key}")


@presets_app.command("list")
def list_presets_cmd(
    format: Annotated[str, typer.Option("--format", "-f", help=FORMAT_HELP)] = "table",
) -> None:
    """List all named screener criteria presets."""
    fmt = parse_format(format)
    rows = [
        {
            "key": p.key,
            "label": p.label,
            "target_metric": p.target_metric,
            "aliases": ", ".join(p.aliases),
        }
        for p in list_presets().values()
    ]
    render(
        rows,
        [
            Column("key", "Key", style="cyan"),
            Column("label", "Label", style="green"),
            Column("target_metric", "Target Metric", style="yellow"),
            Column("aliases", "Aliases"),
        ],
        fmt,
        title="Screener Presets",
    )


@presets_app.command("show")
def show_preset_cmd(name: str) -> None:
    """Show details for a preset."""
    p = resolve_preset_key(name)
    if p is None:
        err_console.print(f"[red]Error:[/red] unknown preset {name!r}")
        raise typer.Exit(code=1)

    render_kv(
        [
            ("Key", p.key),
            ("Label", p.label),
            ("Target Metric", p.target_metric),
            ("Lookback Bars", str(p.lookback_bars)),
            ("Aliases", ", ".join(p.aliases)),
            ("Description", p.description),
        ],
        title=f"Preset: {p.key}",
    )


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


@app.command("explain")
def explain_cmd(
    market: Annotated[str, typer.Option("--market", "-m", help="Market identifier")] = "india",
    types: Annotated[list[str] | None, typer.Option("--type", "-T", help="Asset type(s)")] = None,
    all_listings: Annotated[
        bool, typer.Option("--all-listings", "-a", help="Include non-primary listings")
    ] = False,
    timescale: Annotated[
        str | None, typer.Option("--timescale", "-t", help="Default timeframe")
    ] = None,
    period: Annotated[str | None, typer.Option("--period", "-p", help="Default period")] = None,
    columns: Annotated[
        str | None, typer.Option("--columns", "-c", help="Comma-separated metric keys")
    ] = None,
    column_set: Annotated[
        str | None, typer.Option("--column-set", "-C", help="Named column set")
    ] = None,
    sort: Annotated[
        str | None, typer.Option("--sort", "-s", help="Sort spec, e.g. close:desc")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 50,
    offset: Annotated[int, typer.Option("--offset", "-o", help="Offset")] = 0,
    filters: Annotated[list[str] | None, typer.Argument(help="Trailing filter words")] = None,
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
    market: Annotated[str, typer.Option("--market", "-m", help="Market identifier")] = "india",
    types: Annotated[list[str] | None, typer.Option("--type", "-T", help="Asset type(s)")] = None,
    all_listings: Annotated[
        bool, typer.Option("--all-listings", "-a", help="Include non-primary listings")
    ] = False,
    timescale: Annotated[
        str | None, typer.Option("--timescale", "-t", help="Default timeframe")
    ] = None,
    period: Annotated[str | None, typer.Option("--period", "-p", help="Default period")] = None,
    columns: Annotated[
        str | None, typer.Option("--columns", "-c", help="Comma-separated metric keys")
    ] = None,
    column_set: Annotated[
        str | None, typer.Option("--column-set", "-C", help="Named column set")
    ] = None,
    sort: Annotated[
        str | None, typer.Option("--sort", "-s", help="Sort spec, e.g. close:desc")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 50,
    offset: Annotated[int, typer.Option("--offset", "-o", help="Offset")] = 0,
    print_request: Annotated[
        bool, typer.Option("--print-request", "-P", help="Emit resolved request JSON and exit")
    ] = False,
    format: Annotated[
        str, typer.Option("--format", "-f", help="Output format: table, json, csv, plain")
    ] = "table",
    fetch: Annotated[
        str, typer.Option("--fetch", "-F", help="Missing-data policy: auto, never, force")
    ] = "auto",
    filters: Annotated[list[str] | None, typer.Argument(help="Trailing filter words")] = None,
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

    from honba.cli.data import _DATA_SERVICE
    from honba.screener.service import MissingDataPolicy
    from honba.screener.sources import LocalScreenerSource

    # Configure fetch policy on the shared DataService
    try:
        policy = MissingDataPolicy(fetch.lower())
    except ValueError:
        err_console.print(
            f"[red]Invalid --fetch policy:[/red] {fetch} (choose from auto, never, force)"
        )
        raise typer.Exit(code=1)

    _DATA_SERVICE.policy = policy
    source = LocalScreenerSource(
        data_service=_DATA_SERVICE,
        default_instruments=[
            InstrumentId("RELIANCE", "NSE"),
            InstrumentId("TCS", "NSE"),
        ],
    )

    response = source.scan(req)

    if format.strip().lower() == "json":
        typer.echo(response.model_dump_json(by_alias=True, indent=2))
        return

    rows = [
        {
            "symbol": row.full_symbol,
            "name": row.name,
            **{c: row.values.get(c) for c in response.columns},
        }
        for row in response.rows
    ]
    render(
        rows,
        [
            Column("symbol", "Symbol", style="cyan"),
            Column("name", "Name"),
            *(Column(c, c, align="right", style="green") for c in response.columns),
        ],
        parse_format(format),
        title=f"Screener Results ({response.total} matched)",
    )


@app.command("ask")
def ask_cmd(
    query_text: Annotated[list[str], typer.Argument(help="Free-text query to translate and run")],
    market: Annotated[str, typer.Option("--market", "-m", help="Market identifier")] = "india",
    timescale: Annotated[
        str | None, typer.Option("--timescale", "-t", help="Default timeframe")
    ] = None,
    sort: Annotated[
        str | None, typer.Option("--sort", "-s", help="Sort spec, e.g. close:desc")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 50,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Execute scan without interactive confirmation")
    ] = False,
    format: Annotated[
        str, typer.Option("--format", "-f", help="Output format: table, json, csv, plain")
    ] = "table",
) -> None:
    """Translate natural language into validated filters and run the scan (Design.md Section 13)."""
    from honba.ai.ask import QueryTranslationError, translate_query
    from honba.ai.llm.provider import ScriptedFakeLlm

    full_query = " ".join(query_text).strip()
    if not full_query:
        err_console.print("[red]Query cannot be empty[/red]")
        raise typer.Exit(code=1)

    # Scaffolding: default to ScriptedFakeLlm echoing the query if it contains valid syntax,
    # or returning an error if unconfigured.
    llm = ScriptedFakeLlm(default_response=full_query)
    catalog = load_catalog()

    try:
        result = translate_query(full_query, llm=llm, catalog=catalog)
    except QueryTranslationError as err:
        err_console.print(f"[red]Query translation failed:[/red] {err}")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Translated Filter:[/bold cyan] {result.filter_text}")
    console.print(
        f"[dim]Equivalent command:[/dim] honba screener scan --market {market} {result.filter_text}"
    )

    if not yes:
        confirm = typer.confirm("Run scan with this filter?", default=True)
        if not confirm:
            console.print("[yellow]Scan aborted.[/yellow]")
            return

    # Execute scan with translated filter words
    filter_words = result.filter_text.split()
    scan_cmd(
        market=market,
        types=["EQUITY"],
        all_listings=False,
        timescale=timescale,
        period=None,
        columns=None,
        column_set=None,
        sort=sort,
        limit=limit,
        offset=0,
        print_request=False,
        format=format,
        fetch="auto",
        filters=filter_words,
    )
