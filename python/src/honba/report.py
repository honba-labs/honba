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
tui
    Rich-based terminal UI layout with panels and tables (more visual).
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
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, TextIO


def _format_timestamp_ns(ts_ns: int) -> str:
    """Convert nanosecond timestamp to human-readable UTC date string."""
    if ts_ns <= 0:
        return "?"
    try:
        return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return "?"


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
    format: str = "tui",
    file: TextIO | None = None,
    title: str | None = None,
) -> None:
    """Print ``result`` to ``file`` (default stdout) in the given format.

    Parameters
    ----------
    result:
        Outcome of ``BacktestSession.run()``.
    format:
        ``"table"`` | ``"tui"`` | ``"json"`` | ``"html"``.
    file:
        Output stream; defaults to ``sys.stdout``.
    title:
        Optional heading override (table/html only).
    """
    out = file if file is not None else sys.stdout
    fmt = (format or "table").strip().lower()

    if fmt == "table":
        _print_table(result, out=out, title=title)
    elif fmt == "tui":
        _print_tui(result, out=out, title=title)
    elif fmt == "json":
        _print_json(result, out=out)
    elif fmt == "html":
        _print_html(result, out=out, title=title)
    else:
        raise ValueError(
            f"unknown report format {format!r}; expected 'table', 'tui', 'json', or 'html'"
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
        "equity_curve": [{"ts": int(ts), "equity": float(eq)} for ts, eq in equity_curve],
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
    exchange = _cfg_get(config, "exchange", "?")
    start = _cfg_get(config, "start", "?")
    end = _cfg_get(config, "end", "?")
    timeframe = _cfg_get(config, "timeframe", "?")
    cash = _cfg_get(config, "cash", None)
    currency = _cfg_get(config, "currency", "INR")

    # Prefer Rich when available for aligned columns and mild colour.
    if _try_rich_table(
        out=out,
        heading=heading,
        strategy=strategy,
        symbol=symbol,
        exchange=exchange,
        start=start,
        end=end,
        timeframe=timeframe,
        cash=cash,
        currency=currency,
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
    row("Instrument", f"{symbol}.{exchange}")
    row("Period", f"{start} → {end}")
    row("Timeframe", timeframe)
    if cash is not None:
        row("Initial cash", _fmt_money(cash, currency=currency))
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
    exchange: str,
    start: Any,
    end: Any,
    timeframe: str,
    cash: Any,
    currency: str = "INR",
    metrics: Mapping[str, float],
    fills: Sequence[Any],
    rejections: Sequence[Any],
    notes: Sequence[str],
) -> bool:
    """Render with Rich if importable; return False to fall back to plain text."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        return False

    console = Console(file=out)

    meta = Table(show_header=False, box=None, padding=(0, 2))
    meta.add_column("k", style="dim")
    meta.add_column("v")
    meta.add_row("Strategy", strategy)
    meta.add_row("Instrument", f"{symbol}.{exchange}")
    meta.add_row("Period", f"{start} → {end}")
    meta.add_row("Timeframe", str(timeframe))
    if cash is not None:
        meta.add_row("Initial cash", _fmt_money(cash, currency=currency))

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
    console.print(f"[dim]Fills: {len(fills)}  Rejections: {len(rejections)}[/dim]")

    if fills:
        ftable = Table(title="Recent fills (last 10)", show_header=True)
        ftable.add_column("Time")
        ftable.add_column("Side")
        ftable.add_column("Qty", justify="right")
        ftable.add_column("Price", justify="right")
        for t in fills[-10:]:
            try:
                ts = getattr(t, "ts", 0)
                time_str = _format_timestamp_ns(ts)
            except (ValueError, OSError):
                time_str = "?"
            side = getattr(t, "side", None)
            side_str = side.value.upper() if hasattr(side, "value") else str(side)
            qty = getattr(t, "quantity", 0)
            price = getattr(t, "price", 0)
            ftable.add_row(time_str, side_str, f"{qty:.4f}", f"{price:.2f}")
        console.print(ftable)

    if notes:
        console.print(Panel("\n".join(f"• {n}" for n in notes), title="Notes"))

    return True


# =============================================================================
# TUI (rich-based terminal UI)
# =============================================================================


def _print_tui(
    result: BacktestResult,
    *,
    out: TextIO,
    title: str | None,
) -> None:
    """Render a rich TUI-style report to terminal."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        # Fall back to table
        _print_table(result, out=out, title=title)
        return

    console = Console(file=out)
    strategy = getattr(result, "strategy_name", "?")
    config = getattr(result, "config", None)
    metrics: Mapping[str, float] = getattr(result, "metrics", {}) or {}
    fills = list(getattr(result, "fills", []) or [])
    rejections = list(getattr(result, "rejections", []) or [])
    notes = list(getattr(result, "notes", []) or [])

    heading = title or "Honba Backtest Report"
    symbol = _cfg_get(config, "symbol", "?")
    exchange = _cfg_get(config, "exchange", "?")
    start = _cfg_get(config, "start", "?")
    end = _cfg_get(config, "end", "?")
    timeframe = _cfg_get(config, "timeframe", "?")
    cash = _cfg_get(config, "cash", None)
    currency = _cfg_get(config, "currency", "INR")

    # Header
    header_text = Text()
    header_text.append(f"{heading}\n", style="bold magenta")
    header_text.append(
        f"Strategy: {strategy}  |  Instrument: {symbol}.{exchange}  |  Period: {start} → {end}\n",
        style="dim",
    )
    header_text.append(f"Timeframe: {timeframe}", style="dim")
    if cash is not None:
        header_text.append(f"  |  Initial Cash: {_fmt_money(cash, currency=currency)}", style="dim")
    console.print(Panel(header_text, expand=False))

    # Key metrics
    metric_table = Table(title="Key Metrics", show_header=True, header_style="bold cyan")
    metric_table.add_column("Metric", style="dim")
    metric_table.add_column("Value", justify="right")

    key_metrics = [
        ("total_return_pct", "Total Return %"),
        ("max_drawdown_pct", "Max Drawdown %"),
        ("final_equity", "Final Equity"),
        ("final_cash", "Final Cash"),
        ("n_trades", "Total Trades"),
        ("n_fills", "Fills"),
        ("win_rate", "Win Rate %"),
        ("profit_factor", "Profit Factor"),
        ("sharpe", "Sharpe"),
        ("sortino", "Sortino"),
        ("calmar", "Calmar"),
        ("expectancy_pct", "Expectancy %"),
    ]
    for k, label in key_metrics:
        if k in metrics:
            metric_table.add_row(label, _fmt_metric(k, metrics[k]))

    # Also add any other metrics
    seen = {k for k, _ in key_metrics}
    for k in sorted(metrics):
        if k not in seen:
            metric_table.add_row(_label(k), _fmt_metric(k, metrics[k]))

    console.print(metric_table)

    # Trades table
    if fills:
        trades_table = Table(title=f"Recent Trades (last 20 of {len(fills)})", show_header=True)
        trades_table.add_column("Time")
        trades_table.add_column("Side", style="bold")
        trades_table.add_column("Symbol")
        trades_table.add_column("Qty", justify="right")
        trades_table.add_column("Price", justify="right")
        trades_table.add_column("Costs", justify="right")

        for t in fills[-20:]:
            try:
                ts = getattr(t, "ts", 0)
                time_str = _format_timestamp_ns(ts)
            except (ValueError, OSError):
                time_str = "?"
            side = getattr(t, "side", None)
            side_str = side.value.upper() if hasattr(side, "value") else str(side)
            side_style = "green" if side_str == "BUY" else "red"
            qty = getattr(t, "quantity", 0)
            price = getattr(t, "price", 0)
            costs = getattr(t, "costs", 0)
            inst = getattr(t, "instrument_id", None)
            sym = f"{inst.symbol}.{inst.exchange}" if inst else "?"

            trades_table.add_row(
                time_str,
                Text(side_str, style=side_style),
                sym,
                f"{qty:.4f}",
                f"{price:.2f}",
                f"{costs:.2f}",
            )
        console.print(trades_table)

    if rejections:
        console.print(Panel(f"[yellow]{len(rejections)} rejections[/yellow]", expand=False))

    if notes:
        notes_panel = Panel("\n".join(f"• {n}" for n in notes), title="Notes", expand=False)
        console.print(notes_panel)


# =============================================================================
# Helpers
# =============================================================================


def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    # dataclass or object with attr
    if hasattr(obj, key):
        return getattr(obj, key)
    # dict-like
    if isinstance(obj, dict) and key in obj:
        return obj[key]
    return default


def _fmt_money(v: Any, currency: str = "INR") -> str:
    try:
        from honba.utils.format import format_currency

        return format_currency(v, currency=currency)
    except (ImportError, AttributeError):
        try:
            f = float(v)
            if abs(f) >= 100000:
                return f"₹{f / 100000:.2f}L"
            return f"₹{f:.2f}"
        except (ValueError, TypeError):
            return str(v)


def _fmt_metric(key: str, v: Any) -> str:
    try:
        f = float(v)
    except (ValueError, TypeError):
        return str(v)
    if key.endswith("_pct") or "return" in key or "drawdown" in key:
        return f"{f:+.2f}%"
    if key in ("final_equity", "final_cash", "initial_cash"):
        return _fmt_money(f)
    if f.is_integer():
        return str(int(f))
    return f"{f:.3f}"


def _label(key: str) -> str:
    return key.replace("_", " ").title()


def _config_to_dict(config: Any) -> Any:
    if config is None:
        return None
    if is_dataclass(config) and not isinstance(config, type):
        return asdict(config)
    if hasattr(config, "__dict__"):
        return dict(config.__dict__)
    if isinstance(config, dict):
        return config
    return str(config)


def _trade_to_dict(t: Any) -> dict[str, Any]:
    if hasattr(t, "to_dict"):
        try:
            return t.to_dict()
        except (AttributeError, ValueError):
            pass
    d: dict[str, Any] = {}
    for k in ("instrument_id", "side", "quantity", "price", "ts", "order_id", "costs"):
        if hasattr(t, k):
            v = getattr(t, k)
            if hasattr(v, "value"):
                d[k] = v.value
            elif hasattr(v, "symbol"):
                d[k] = f"{v.symbol}.{v.exchange}" if hasattr(v, "exchange") else str(v)
            else:
                d[k] = v
    return d


def _rejection_to_dict(r: Any) -> dict[str, Any]:
    if hasattr(r, "to_dict"):
        try:
            return r.to_dict()
        except (AttributeError, ValueError):
            pass
    if hasattr(r, "__dict__"):
        return dict(r.__dict__)
    return str(r)


def _format_fill_line(t: Any) -> str:
    try:
        ts = getattr(t, "ts", "?")
        side = getattr(t, "side", None)
        side_str = side.value.upper() if hasattr(side, "value") else str(side)
        qty = getattr(t, "quantity", 0)
        price = getattr(t, "price", 0)
        inst = getattr(t, "instrument_id", None)
        sym = f"{inst.symbol}" if inst else ""
        return f"{ts} {side_str} {qty:.4f} {sym}@{price:.2f}"
    except (ValueError, TypeError, AttributeError):
        return str(t)


def _print_json(result: BacktestResult, *, out: TextIO) -> None:
    print(json.dumps(result_to_dict(result), indent=2, default=str), file=out)


def _print_html(result: BacktestResult, *, out: TextIO, title: str | None) -> None:
    heading = title or "Honba backtest report"
    d = result_to_dict(result)
    html = f"<h1>{heading}</h1><pre>{json.dumps(d, indent=2, default=str)}</pre>"
    print(html, file=out)
