"""Data management commands (coverage, gaps, fetch) (Design.md Section 12.5)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.ports import InMemoryBarStore, InMemoryMarketDataProvider
from honba.screener.store import ParquetBarStore
from honba.research.data_loader.nse import NseBhavcopyProvider
from honba.screener.service import DataService

app = typer.Typer(help="Data inspection and fetch commands: coverage, gaps, fetch", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)

# Shared in-memory / local storage instances for CLI session
# Persistent Parquet and ledger store located in project root honba/data or ./data
_STORE = ParquetBarStore()
_NSE_PROVIDER = NseBhavcopyProvider(cache_dir=_STORE.data_dir / "cache" / "bhavcopy")
_MOCK_PROVIDER = InMemoryMarketDataProvider()

# Bhavcopy provider is primary for real market data; mock provider is fallback
_DATA_SERVICE = DataService(store=_STORE, providers=[_NSE_PROVIDER, _MOCK_PROVIDER])


@app.command("coverage")
def coverage_cmd(
    symbol: Annotated[str | None, typer.Argument(help="Optional symbol, e.g. RELIANCE")] = None,
    exchange: Annotated[str, typer.Option("--exchange", "-e", "--venue", help="Market exchange / venue [default: NSE]")] = "NSE",
    timeframe: Annotated[str, typer.Option("--timeframe", "-t", help="Timeframe [default: 1D]")] = "1D",
) -> None:
    """Show covered ranges in the data store."""
    table = Table(title="Data Store Coverage")
    table.add_column("Exchange", style="cyan")
    table.add_column("Symbol", style="green")
    table.add_column("Timeframe", style="magenta")
    table.add_column("Interval", style="yellow")
    table.add_column("Status", style="white")
    table.add_column("Rows", justify="right")

    instruments = [InstrumentId(symbol, exchange)] if symbol else [
        InstrumentId("RELIANCE", exchange),
        InstrumentId("TCS", exchange),
    ]

    found = False
    for inst in instruments:
        records = _STORE.coverage(inst, timeframe)
        for r in records:
            found = True
            table.add_row(
                r.venue,
                r.symbol,
                r.timeframe,
                f"{r.interval.start}..{r.interval.end}",
                r.status.value,
                str(r.row_count),
            )

    if not found:
        console.print(f"[yellow]No covered intervals found for {symbol or 'all symbols'}[/yellow]")
    else:
        console.print(table)


@app.command("gaps")
def gaps_cmd(
    symbol: Annotated[str, typer.Argument(help="Instrument symbol, e.g. RELIANCE")],
    exchange: Annotated[str, typer.Option("--exchange", "-e", "--venue", help="Market exchange / venue [default: NSE]")] = "NSE",
    timeframe: Annotated[str, typer.Option("--timeframe", "-t", help="Timeframe [default: 1D]")] = "1D",
    start: Annotated[str, typer.Option("--start", "-s", help="Start date YYYY-MM-DD", show_default="2024-01-01")] = "2024-01-01",
    end: Annotated[str | None, typer.Option("--end", "-d", help="End date YYYY-MM-DD", show_default="today")] = None,
) -> None:
    """Show missing ranges that a request would fetch."""
    inst = InstrumentId(symbol, exchange)
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today() + dt.timedelta(days=1)
    req_interval = DateInterval(start_date, end_date)

    plan = _DATA_SERVICE.plan([inst], timeframe, req_interval)
    gaps = plan.gaps_by_instrument.get(inst, [])

    if not gaps:
        console.print(f"[green]No gaps found for {symbol} ({start_date}..{end_date})[/green]")
        return

    table = Table(title=f"Missing Gaps for {symbol}.{exchange} ({timeframe})")
    table.add_column("Gap Start", style="yellow")
    table.add_column("Gap End", style="yellow")

    for g in gaps:
        table.add_row(str(g.start), str(g.end))

    console.print(table)


@app.command("fetch")
def fetch_cmd(
    symbol: Annotated[str, typer.Argument(help="Instrument symbol, e.g. RELIANCE")],
    exchange: Annotated[str, typer.Option("--exchange", "-e", "--venue", help="Market exchange / venue [default: NSE]")] = "NSE",
    timeframe: Annotated[str, typer.Option("--timeframe", "-t", help="Timeframe [default: 1D]")] = "1D",
    start: Annotated[str, typer.Option("--start", "-s", help="Start date YYYY-MM-DD", show_default="2024-01-01")] = "2024-01-01",
    end: Annotated[str | None, typer.Option("--end", "-d", help="End date YYYY-MM-DD", show_default="today")] = None,
) -> None:
    """Fetch missing data and fill gaps without running a scan."""
    inst = InstrumentId(symbol, exchange)
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today() + dt.timedelta(days=1)
    req_interval = DateInterval(start_date, end_date)

    plan = _DATA_SERVICE.plan([inst], timeframe, req_interval)
    gaps = plan.gaps_by_instrument.get(inst, [])

    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    if not gaps:
        console.print(f"[green]Data already complete for {symbol}[/green]")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total} sessions)"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(f"Fetching {symbol}.{exchange}...", total=None)

        def on_progress(session_date: dt.date, current: int, total: int) -> None:
            if progress.tasks[task_id].total != total:
                progress.update(task_id, total=total)
            progress.update(
                task_id,
                completed=current,
                description=f"Fetching {symbol}.{exchange} ({session_date.strftime('%Y-%m-%d')})",
            )

        res = _DATA_SERVICE.ensure(plan, progress_callback=on_progress)

    if res.success:
        total_bars = sum(len(b) for b in res.bars.values())
        console.print(f"[green]Successfully fetched {len(gaps)} gap(s) ({total_bars} bars stored in {_STORE.catalog_dir}) for {symbol}.{exchange}[/green]")
    else:
        err_console.print(f"[red]Failed to fill gaps:[/red] {res.warnings}")
        raise typer.Exit(code=2)
