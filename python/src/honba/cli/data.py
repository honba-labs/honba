"""Data management commands (coverage, gaps, fetch) (Design.md Section 12.5)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import typer
from rich.console import Console

from honba.cli._output import FORMAT_HELP, parse_format
from honba.data.loaders.yfinance import YFinanceProvider
from honba.display import Column, OutputFormat, render
from honba.entities.instrument import InstrumentId
from honba.research.data_loader.nse import NseBhavcopyProvider
from honba.screener.coverage import DateInterval
from honba.screener.ports import InMemoryMarketDataProvider
from honba.screener.service import DataService
from honba.screener.store import ParquetBarStore

app = typer.Typer(
    help="Data inspection and fetch commands: coverage, gaps, fetch", no_args_is_help=True
)
console = Console()
err_console = Console(stderr=True)

# Shared in-memory / local storage instances for CLI session
# Persistent Parquet and ledger store located in project root honba/data or ./data
_STORE = ParquetBarStore()
_NSE_PROVIDER = NseBhavcopyProvider(cache_dir=_STORE.data_dir / "cache" / "bhavcopy")
_YFINANCE_PROVIDER = YFinanceProvider()
_MOCK_PROVIDER = InMemoryMarketDataProvider()

# Default service: NSE bhavcopy first, yfinance fallback for daily / primary for intraday
_DATA_SERVICE = DataService(
    store=_STORE, providers=[_NSE_PROVIDER, _YFINANCE_PROVIDER, _MOCK_PROVIDER]
)


@app.command("coverage")
def coverage_cmd(
    symbol: Annotated[str | None, typer.Argument(help="Optional symbol, e.g. RELIANCE")] = None,
    exchange: Annotated[
        str,
        typer.Option(
            "--exchange", "-e", "--exchange", help="Market exchange / exchange [default: NSE]"
        ),
    ] = "NSE",
    timeframe: Annotated[
        str, typer.Option("--timeframe", "-t", help="Timeframe [default: 1D]")
    ] = "1D",
    format: Annotated[str, typer.Option("--format", "-f", help=FORMAT_HELP)] = "table",
) -> None:
    """Show covered ranges in the data store."""
    fmt = parse_format(format)
    rows: list[dict[str, object]] = []

    instruments = (
        [InstrumentId(symbol, exchange)]
        if symbol
        else [
            InstrumentId("RELIANCE", exchange),
            InstrumentId("TCS", exchange),
        ]
    )

    for inst in instruments:
        for r in _STORE.coverage(inst, timeframe):
            rows.append(
                {
                    "exchange": r.exchange,
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "interval": f"{r.interval.start}..{r.interval.end}",
                    "status": r.status.value,
                    "rows": r.row_count,
                }
            )

    if not rows and fmt is OutputFormat.TABLE:
        console.print(f"[yellow]No covered intervals found for {symbol or 'all symbols'}[/yellow]")
        return
    render(
        rows,
        [
            Column("exchange", "Exchange", style="cyan"),
            Column("symbol", "Symbol", style="green"),
            Column("timeframe", "Timeframe", style="magenta"),
            Column("interval", "Interval", style="yellow"),
            Column("status", "Status"),
            Column("rows", "Rows"),
        ],
        fmt,
        title="Data Store Coverage",
    )


@app.command("gaps")
def gaps_cmd(
    symbol: Annotated[str, typer.Argument(help="Instrument symbol, e.g. RELIANCE")],
    exchange: Annotated[
        str,
        typer.Option(
            "--exchange", "-e", "--exchange", help="Market exchange / exchange [default: NSE]"
        ),
    ] = "NSE",
    timeframe: Annotated[
        str, typer.Option("--timeframe", "-t", help="Timeframe [default: 1D]")
    ] = "1D",
    start: Annotated[
        str, typer.Option("--start", "-s", help="Start date YYYY-MM-DD", show_default="2024-01-01")
    ] = "2024-01-01",
    end: Annotated[
        str | None, typer.Option("--end", "-d", help="End date YYYY-MM-DD", show_default="today")
    ] = None,
    format: Annotated[str, typer.Option("--format", "-f", help=FORMAT_HELP)] = "table",
) -> None:
    """Show missing ranges that a request would fetch."""
    fmt = parse_format(format)
    inst = InstrumentId(symbol, exchange)
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today() + dt.timedelta(days=1)  # noqa: DTZ011 - CLI default 'today' is the user's local calendar date
    req_interval = DateInterval(start_date, end_date)

    plan = _DATA_SERVICE.plan([inst], timeframe, req_interval)
    gaps = plan.gaps_by_instrument.get(inst, [])

    if not gaps and fmt is OutputFormat.TABLE:
        console.print(f"[green]No gaps found for {symbol} ({start_date}..{end_date})[/green]")
        return

    render(
        [{"gap_start": str(g.start), "gap_end": str(g.end)} for g in gaps],
        [
            Column("gap_start", "Gap Start", style="yellow"),
            Column("gap_end", "Gap End", style="yellow"),
        ],
        fmt,
        title=f"Missing Gaps for {symbol}.{exchange} ({timeframe})",
    )


@app.command("fetch")
def fetch_cmd(
    symbol: Annotated[str, typer.Argument(help="Instrument symbol, e.g. RELIANCE")],
    exchange: Annotated[
        str,
        typer.Option(
            "--exchange", "-e", "--exchange", help="Market exchange / exchange [default: NSE]"
        ),
    ] = "NSE",
    timeframe: Annotated[
        str, typer.Option("--timeframe", "-t", help="Timeframe [default: 1D]")
    ] = "1D",
    start: Annotated[
        str, typer.Option("--start", "-s", help="Start date YYYY-MM-DD", show_default="2024-01-01")
    ] = "2024-01-01",
    end: Annotated[
        str | None, typer.Option("--end", "-d", help="End date YYYY-MM-DD", show_default="today")
    ] = None,
    provider: Annotated[
        str, typer.Option("--provider", "-p", help="Provider: auto, yfinance, nse [default: auto]")
    ] = "auto",
) -> None:
    """Fetch missing data and fill gaps without running a scan."""
    inst = InstrumentId(symbol, exchange)
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today() + dt.timedelta(days=1)  # noqa: DTZ011 - CLI default 'today' is the user's local calendar date
    req_interval = DateInterval(start_date, end_date)

    p_clean = provider.lower().strip()
    if p_clean == "yfinance":
        svc = DataService(store=_STORE, providers=[_YFINANCE_PROVIDER])
    elif p_clean == "nse":
        svc = DataService(store=_STORE, providers=[_NSE_PROVIDER])
    elif timeframe.upper() not in ("1D", "D", "DAILY"):
        # Intraday default to yfinance
        svc = DataService(store=_STORE, providers=[_YFINANCE_PROVIDER])
    else:
        svc = _DATA_SERVICE

    plan = svc.plan([inst], timeframe, req_interval)
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

        res = svc.ensure(plan, progress_callback=on_progress)

    if res.success:
        total_bars = sum(len(b) for b in res.bars.values())
        console.print(
            f"[green]Successfully fetched {len(gaps)} gap(s) ({total_bars} bars stored in {_STORE.catalog_dir}) for {symbol}.{exchange}[/green]"
        )
    else:
        err_console.print(f"[red]Failed to fill gaps:[/red] {res.warnings}")
        raise typer.Exit(code=2)


@app.command("housekeeping")
def housekeeping_cmd() -> None:
    """Run housekeeping on data store: compress bhavcopy cache to .xz and consolidate coverage ledger."""
    console.print("[cyan]Running data cache housekeeping & xz compression...[/cyan]")
    count, orig, comp = _NSE_PROVIDER.compress_existing_cache()
    if count > 0:
        saved = orig - comp
        ratio = (comp / orig * 100) if orig else 0
        console.print(
            f"[green]Compressed {count} cache file(s): "
            f"{orig / (1024 * 1024):.1f}MB -> {comp / (1024 * 1024):.1f}MB "
            f"({ratio:.1f}%, saved {saved / (1024 * 1024):.1f}MB)[/green]"
        )
    else:
        console.print("[green]Cache files are already compressed in .xz format.[/green]")

    # Ledger interval consolidation
    ledger = _STORE._load_ledger()
    consolidated_entries = 0
    for k, records in ledger.items():
        before_len = len(records)
        ledger[k] = _STORE._merge_ledger_entries(records)
        consolidated_entries += before_len - len(ledger[k])
    _STORE._save_ledger(ledger)
    console.print(
        f"[green]Ledger consolidated: merged {consolidated_entries} overlapping/contiguous records.[/green]"
    )
