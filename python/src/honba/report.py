"""Backtest (and later paper/live) report presentation.

Session produces a ``BacktestResult``. This module only formats and prints it.
No data loading, no strategy execution, no metrics invention beyond what the
result already carries — presentation stays replaceable without touching the
engine.

Formats
-------
table
    Human-readable stdout (default for ``honba run``). Uses Rich when
    installed; plain text otherwise.
json
    Machine-readable dump of config + metrics + trades (for scripts / CI).
html
    Lightweight HTML fragment (notebooks / research); optional.

CLI wiring::

    result = Honba.backtest(...).run()
    print_backtest_report(result, format="table")

Or via the result helper::

    result.print_report(format="json")
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence, TextIO

# BacktestResult is defined in session; import for type checkers only at
# runtime we accept any object with the expected attributes (duck typing)
# so report can be used in tests without importing the full session graph.
try:
    from honba.session import BacktestResult
except ImportError:  # pragma: no cover — bootstrap / circular-import guard
    BacktestResult = Any  # type: ignore[misc, assignment]


# =============================================================================
# Public API
# =============================================================================


def print_backtest_report(
    result: BacktestResult,
    *,
    format: str = "table",
    file: TextIO | None = None,
    title: str | None = None,
) -> None:
    """Print ``result`` to ``file`` (default stdout) in the given format.

    Parameters
    ----------
    result:
        Outcome of ``BacktestSession.run()``.
    format:
        ``"table"`` | ``"json"`` | ``"html"``.
    file:
        Output stream; defaults to ``sys.stdout``.
    title:
        Optional heading override (table/html only).
    """
    out = file if file is not None else sys.stdout
    fmt = (format or "table").strip().lower()

    if fmt == "table":
        _print_table(result, out=out, title=title)
    elif fmt == "json":
        _print_json(result, out=out)
    elif fmt == "html":
        _print_html(result, out=out, title=title)
    else:
        raise ValueError(
            f"unknown report format {format!r}; expected 'table', 'json', or 'html'"
        )


def result_to_dict(result: BacktestResult) -> dict[str, Any]:
    """Serialize a result to a JSON-friendly dict.

    Used by the json formatter and by callers who want structured data
    without printing. Keeps field names stable for CI and external tools.
    """
    config = getattr(result, "config", None)
    metrics = dict(getattr(result, "metrics", {}) or {})
    fills = list(getattr(result, "fills", []) or [])
    rejections = list(getattr(result, "rejections", []) or [])
    notes = list(getattr(result, "notes", []) or [])
    equity_curve = list(getattr(result, "equity_curve", []) or [])

    return {
        "strategy": getattr(result, "strategy_name", None),
        "config": _config_to_dict(config),
        "metrics": metrics,
        "n_fills": len(fills),
        "n_rejections": len(rejections),
        "fills": [_trade_to_dict(t) for t in fills],
        "rejections": [_rejection_to_dict(r) for r in rejections],
        "equity_curve": [
            {"ts": int(ts), "equity": float(eq)} for ts, eq in equity_curve
        ],
        "notes": notes,
    }


# =============================================================================
# Table (human)
# =============================================================================


def _print_table(
    result: BacktestResult,
    *,
    out: TextIO,
    title: str | None,
) -> None:
    """Stdout summary: header, metrics block, optional trade tail."""
    strategy = getattr(result, "strategy_name", "?")
    config = getattr(result, "config", None)
    metrics: Mapping[str, float] = getattr(result, "metrics", {}) or {}
    fills = list(getattr(result, "fills", []) or [])
    rejections = list(getattr(result, "rejections", []) or [])
    notes = list(getattr(result, "notes", []) or [])

    heading = title or "Honba backtest report"
    symbol = _cfg_get(config, "symbol", "?")
    venue = _cfg_get(config, "venue", "?")
    start = _cfg_get(config, "start", "?")
    end = _cfg_get(config, "end", "?")
    timeframe = _cfg_get(config, "timeframe", "?")
    cash = _cfg_get(config, "cash", None)

    # Prefer Rich when available for aligned columns and mild colour.
    if _try_rich_table(
        out=out,
        heading=heading,
        strategy=strategy,
        symbol=symbol,
        venue=venue,
        start=start,
        end=end,
        timeframe=timeframe,
        cash=cash,
        metrics=metrics,
        fills=fills,
        rejections=rejections,
        notes=notes,
    ):
        return

    # ---- Plain-text fallback -------------------------------------------------
    width = 56
    line = "=" * width
    thin = "-" * width

    def row(label: str, value: Any) -> None:
        print(f"  {label:<22} {value}", file=out)

    print(line, file=out)
    print(f"  {heading}", file=out)
    print(line, file=out)
    row("Strategy", strategy)
    row("Instrument", f"{symbol}.{venue}")
    row("Period", f"{start} → {end}")
    row("Timeframe", timeframe)
    if cash is not None:
        row("Initial cash", _fmt_money(cash))
    print(thin, file=out)

    # Metrics — fixed order first, then any extras
    preferred = [
        "final_equity",
        "final_cash",
        "total_return_pct",
        "max_drawdown_pct",
        "n_trades",
        "n_fills",
        "initial_cash",
    ]
    seen: set[str] = set()
    for key in preferred:
        if key in metrics:
            row(_label(key), _fmt_metric(key, metrics[key]))
            seen.add(key)
    for key in sorted(metrics.keys()):
        if key not in seen:
            row(_label(key), _fmt_metric(key, metrics[key]))

    print(thin, file=out)
    row("Fills", len(fills))
    row("Rejections", len(rejections))

    if fills:
        print(thin, file=out)
        print("  Recent fills (last 10)", file=out)
        for t in fills[-10:]:
            print(f"    {_format_fill_line(t)}", file=out)

    if notes:
        print(thin, file=out)
        print("  Notes", file=out)
        for n in notes:
            print(f"    • {n}", file=out)

    print(line, file=out)


def _try_rich_table(
    *,
    out: TextIO,
    heading: str,
    strategy: str,
    symbol: str,
    venue: str,
    start: Any,
    end: Any,
    timeframe: str,
    cash: Any,
    metrics: Mapping[str, float],
    fills: Sequence[Any],
    rejections: Sequence[Any],
    notes: Sequence[str],
) -> bool:
    """Render with Rich if importable; return False to fall back to plain text."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:
        return False

    console = Console(file=out)

    meta = Table(show_header=False, box=None, padding=(0, 2))
    meta.add_column("k", style="dim")
    meta.add_column("v")
    meta.add_row("Strategy", strategy)
    meta.add_row("Instrument", f"{symbol}.{venue}")
    meta.add_row("Period", f"{start} → {end}")
    meta.add_row("Timeframe", str(timeframe))
    if cash is not None:
        meta.add_row("Initial cash", _fmt_money(cash))

    mtable = Table(title="Metrics", show_header=True, header_style="bold")
    mtable.add_column("Metric")
    mtable.add_column("Value", justify="right")
    preferred = [
        "final_equity",
        "final_cash",
        "total_return_pct",
        "max_drawdown_pct",
        "n_trades",
        "n_fills",
    ]
    seen: set[str] = set()
    for key in preferred:
        if key in metrics:
            mtable.add_row(_label(key), _fmt_metric(key, metrics[key]))
            seen.add(key)
    for key in sorted(metrics.keys()):
        if key not in seen:
            mtable.add_row(_label(key), _fmt_metric(key, metrics[key]))

    console.print(Panel(Text(heading, style="bold"), expand=False))
    console.print(meta)
    console.print(mtable)
    console.print(
        f"[dim]Fills: {len(fills)}  Rejections: {len(rejections)}[/dim]"
    )

    if fills:
        ftable = Table(title="Recent fills (last 10)", show_header=True)
        ftable.add_column("Time")
        ftable.add_column("Side")
        ftable.add_column("Qty", justify="right")
        ftable.add_column("Price", justify="right")