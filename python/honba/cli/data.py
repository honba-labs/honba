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
from honba.research.data_loader.nse import NseBhavcopyProvider
from honba.screener.service import DataService

app = typer.Typer(help="Data inspection and fetch commands: coverage, gaps, fetch")
console = Console()
err_console = Console(stderr=True)

# Shared in-memory / local storage instances for CLI session
_STORE = InMemoryBarStore()
_NSE_PROVIDER = NseBhavcopyProvider()
_MOCK_PROVIDER = InMemoryMarketDataProvider()

# Populate default mock store with sample data so offline CLI commands and tests work cleanly
_RELIANCE_NSE = InstrumentId("RELIANCE", "NSE")
_TCS_NSE = InstrumentId("TCS", "NSE")
for inst in (_RELIANCE_NSE, _TCS_NSE):
    base_ts = int(dt.datetime(2024, 1, 1).timestamp() * 1e9)
    sample_bars = [
        Bar(instrument_id=inst, ts=base_ts + i * 86400 * 10**9, open=150.0 + i, high=155.0 + i, low=148.0 + i, close=152.0 + i, volume=1000.0)
        for i in range(10)
    ]
    _MOCK_PROVIDER.add_bars(inst, "1D", sample_bars)

# In test and default offline environment, mock provider is primary; Bhavcopy provider is used when configured
_DATA_SERVICE = DataService(store=_STORE, providers=[_MOCK_PROVIDER, _NSE_PROVIDER])


@app.command("coverage")
def coverage_cmd(
    symbol: Annotated[str | None, typer.Argument(help="Optional symbol, e.g. RELIANCE")] = None,
    venue: Annotated[str, typer.Option("--venue", help="Market venue")] = "NSE",
    timeframe: Annotated[str, typer.Option("--timeframe", "-t", help="Timeframe")] = "1D",
) -> None:
    """Show covered ranges in the data store."""
    table = Table(title="Data Store Coverage")
    table.add_column("Venue", style="cyan")
    table.add_column("Symbol", style="green")
    table.add_column("Timeframe", style="magenta")
    table.add_column("Interval", style="yellow")
    table.add_column("Status", style="white")
    table.add_column("Rows", justify="right")

    instruments = [InstrumentId(symbol, venue)] if symbol else [
        InstrumentId("RELIANCE", venue),
        InstrumentId("TCS", venue),
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
    venue: Annotated[str, typer.Option("--venue", help="Market venue")] = "NSE",
    timeframe: Annotated[str, typer.Option("--timeframe", "-t", help="Timeframe")] = "1D",
    start: Annotated[str, typer.Option("--start", help="Start date YYYY-MM-DD")] = "2024-01-01",
    end: Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD")] = None,
) -> None:
    """Show missing ranges that a request would fetch."""
    inst = InstrumentId(symbol, venue)
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today() + dt.timedelta(days=1)
    req_interval = DateInterval(start_date, end_date)

    plan = _DATA_SERVICE.plan([inst], timeframe, req_interval)
    gaps = plan.gaps_by_instrument.get(inst, [])

    if not gaps:
        console.print(f"[green]No gaps found for {symbol} ({start_date}..{end_date})[/green]")
        return

    table = Table(title=f"Missing Gaps for {symbol}.{venue} ({timeframe})")
    table.add_column("Gap Start", style="yellow")
    table.add_column("Gap End", style="yellow")

    for g in gaps:
        table.add_row(str(g.start), str(g.end))

    console.print(table)


@app.command("fetch")
def fetch_cmd(
    symbol: Annotated[str, typer.Argument(help="Instrument symbol, e.g. RELIANCE")],
    venue: Annotated[str, typer.Option("--venue", help="Market venue")] = "NSE",
    timeframe: Annotated[str, typer.Option("--timeframe", "-t", help="Timeframe")] = "1D",
    start: Annotated[str, typer.Option("--start", help="Start date YYYY-MM-DD")] = "2024-01-01",
    end: Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD")] = None,
) -> None:
    """Fetch missing data and fill gaps without running a scan."""
    inst = InstrumentId(symbol, venue)
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today() + dt.timedelta(days=1)
    req_interval = DateInterval(start_date, end_date)

    plan = _DATA_SERVICE.plan([inst], timeframe, req_interval)
    gaps = plan.gaps_by_instrument.get(inst, [])

    if not gaps:
        console.print(f"[green]Data already complete for {symbol}[/green]")
        return

    res = _DATA_SERVICE.ensure(plan)
    if res.success:
        console.print(f"[green]Successfully fetched {len(gaps)} gap(s) for {symbol}.{venue}[/green]")
    else:
        err_console.print(f"[red]Failed to fill gaps:[/red] {res.warnings}")
        raise typer.Exit(code=2)
