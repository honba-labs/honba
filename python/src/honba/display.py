"""Shared table and key-value display for the CLI, reports, examples and strategies.

Presentation only (edge layer): no domain imports, no wall clock, no I/O beyond the
stream you pass in. One helper renders rows of data in four formats:

``table``
    Rich table when Rich is importable, otherwise the plain layout.
``plain``
    Aligned plain text, same columns as ``table``, no box drawing.
``json``
    A list of objects keyed by column key, in column order, raw (unformatted) values.
``csv``
    Header row of column keys, then one row per record, raw values.

Colour is only emitted when the stream is a TTY and ``NO_COLOR`` is unset; the layout is
identical either way. Output is deterministic for a given ``width``.

Usage::

    from honba.display import Column, render_kv, render_table

    render_table(rows, ["symbol", Column("qty", "Qty"), Column("price", fmt=format_price)])
    render_kv({"Strategy": "sma", "Cash": format_money(1_000_000)}, title="Run")
"""

from __future__ import annotations

import csv
import json
import os
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any, TextIO

__all__ = [
    "Column",
    "OutputFormat",
    "format_cell",
    "format_money",
    "format_percent",
    "format_timestamp_ns",
    "render",
    "render_kv",
    "render_table",
]

_ELLIPSIS = "…"
_GUTTER = "  "


# =============================================================================
# Cell formatting
# =============================================================================


def format_timestamp_ns(ts_ns: int) -> str:
    """Convert a Unix-nanosecond timestamp to a UTC ``YYYY-MM-DD`` string (``?`` if invalid)."""
    if ts_ns <= 0:
        return "?"
    try:
        return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return "?"


def format_money(v: Any, currency: str = "INR") -> str:
    """Format an amount with its currency symbol (Indian grouping for INR)."""
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


def format_percent(v: Any, decimals: int = 2) -> str:
    """Format ``v`` (already in percent units) with an explicit sign, e.g. ``+12.50%``."""
    try:
        return f"{float(v):+.{decimals}f}%"
    except (ValueError, TypeError):
        return str(v)


def format_cell(v: Any) -> str:
    """Default cell text: ``-`` for None, two decimals for floats, ``str`` otherwise."""
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


# =============================================================================
# Specs
# =============================================================================


@dataclass(frozen=True)
class Column:
    """One output column.

    ``key`` selects the value from a mapping row (or is just a label for positional rows) and
    is the key used in json/csv output. ``align`` is ``left``/``right``/``center``; when unset,
    columns whose values are all numbers right-align. ``fmt`` turns a raw value into cell text
    (table and plain only). ``max_width`` truncates cells with an ellipsis.
    """

    key: str
    header: str | None = None
    align: str | None = None
    fmt: Callable[[Any], str] | None = None
    style: str | None = None
    max_width: int | None = None

    @property
    def title(self) -> str:
        return self.key if self.header is None else self.header


class OutputFormat(str, Enum):
    """Output formats understood by :func:`render`."""

    TABLE = "table"
    JSON = "json"
    CSV = "csv"
    PLAIN = "plain"

    @classmethod
    def parse(cls, value: OutputFormat | str) -> OutputFormat:
        """Parse a format name (case-insensitive); ``ValueError`` if unknown."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            allowed = ", ".join(f.value for f in cls)
            raise ValueError(
                f"unknown output format {value!r}; expected one of: {allowed}"
            ) from None


ColumnSpec = Column | str
Row = Mapping[str, Any] | Sequence[Any]


def _columns(columns: Iterable[ColumnSpec]) -> list[Column]:
    return [c if isinstance(c, Column) else Column(str(c)) for c in columns]


def _raw(row: Row, index: int, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index] if index < len(row) else None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _align(col: Column, values: Sequence[Any]) -> str:
    if col.align:
        return col.align
    present = [v for v in values if v is not None]
    return "right" if present and all(_is_number(v) for v in present) else "left"


def _cells(rows: Sequence[Row], cols: Sequence[Column]) -> tuple[list[list[str]], list[str]]:
    """Formatted cell text per row, and the alignment of each column."""
    text: list[list[str]] = []
    for row in rows:
        line = []
        for i, col in enumerate(cols):
            v = _raw(row, i, col.key)
            if v is None:
                line.append("-")
            else:
                line.append((col.fmt or format_cell)(v))
        text.append(line)
    aligns = [_align(c, [_raw(r, i, c.key) for r in rows]) for i, c in enumerate(cols)]
    return text, aligns


# =============================================================================
# Terminal helpers
# =============================================================================


def _use_color(out: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(out, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except (ValueError, OSError):
        return False


def _cell_width(text: str) -> int:
    """Terminal cells occupied by ``text`` (wide East Asian glyphs count 2, combining 0)."""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _truncate(text: str, width: int) -> str:
    if _cell_width(text) <= width:
        return text
    if width <= 1:
        return _ELLIPSIS[:width]
    out, used = [], 0
    for ch in text:
        w = _cell_width(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + _ELLIPSIS


def _pad(text: str, width: int, align: str) -> str:
    gap = max(0, width - _cell_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def _load_rich() -> Any | None:
    """Return a namespace of the Rich classes used here, or None when Rich is missing."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        return None

    return SimpleNamespace(Console=Console, Table=Table, Text=Text)


def _console(rich: Any, out: TextIO, width: int | None) -> Any:
    color = _use_color(out)
    return rich.Console(
        file=out,
        width=width,
        force_terminal=color,
        color_system="auto" if color else None,
        no_color=not color,
        highlight=False,
        markup=False,
        emoji=False,
    )


# =============================================================================
# Table
# =============================================================================


def render_table(
    rows: Sequence[Row],
    columns: Sequence[ColumnSpec],
    *,
    title: str | None = None,
    out: TextIO | None = None,
    width: int | None = None,
    plain: bool = False,
) -> None:
    """Print ``rows`` as a table to ``out`` (default ``sys.stdout``).

    ``rows`` are mappings keyed by column key, or positional sequences. ``width`` fixes the
    layout width (use it for snapshots); ``plain=True`` skips Rich.
    """
    stream = out if out is not None else sys.stdout
    cols = _columns(columns)
    rows = list(rows)
    rich = None if plain else _load_rich()
    if rich is None:
        _plain_table(rows, cols, title, stream, width)
        return

    text, aligns = _cells(rows, cols)
    color = _use_color(stream)
    table = rich.Table(
        title=title,
        show_header=True,
        header_style="bold" if color else "",
    )
    for col, align in zip(cols, aligns, strict=True):
        table.add_column(
            col.title,
            justify=align,
            style=col.style if color and col.style else "",
            max_width=col.max_width,
            overflow="ellipsis",
        )
    for line in text:
        table.add_row(*(rich.Text(cell) for cell in line))
    _console(rich, stream, width).print(table)


def _plain_table(
    rows: Sequence[Row],
    cols: Sequence[Column],
    title: str | None,
    out: TextIO,
    width: int | None,
) -> None:
    text, aligns = _cells(rows, cols)
    headers = [c.title for c in cols]
    for line in text:
        for i, col in enumerate(cols):
            if col.max_width:
                line[i] = _truncate(line[i], col.max_width)
    widths = [
        max([_cell_width(headers[i])] + [_cell_width(line[i]) for line in text])
        for i in range(len(cols))
    ]
    if width is not None:
        budget = width - len(_GUTTER) * max(0, len(cols) - 1)
        while sum(widths) > budget and max(widths, default=0) > 3:
            widths[widths.index(max(widths))] -= 1

    def fmt_line(cells: Sequence[str], alignment: Sequence[str]) -> str:
        parts = [
            _pad(_truncate(c, w), w, a) for c, w, a in zip(cells, widths, alignment, strict=True)
        ]
        return _GUTTER.join(parts).rstrip()

    if title:
        print(_truncate(title, width) if width else title, file=out)
    print(fmt_line(headers, ["left" if a != "right" else "right" for a in aligns]), file=out)
    print(_GUTTER.join("-" * w for w in widths), file=out)
    for line in text:
        print(fmt_line(line, aligns), file=out)


# =============================================================================
# Key-value
# =============================================================================


def render_kv(
    pairs: Mapping[str, Any] | Iterable[tuple[str, Any]],
    *,
    title: str | None = None,
    out: TextIO | None = None,
    width: int | None = None,
    plain: bool = False,
) -> None:
    """Print ``key  value`` pairs, keys aligned, in the given order."""
    stream = out if out is not None else sys.stdout
    items = list(pairs.items() if isinstance(pairs, Mapping) else pairs)
    rows = [(str(k), format_cell(v)) for k, v in items]
    rich = None if plain else _load_rich()
    if rich is None:
        key_w = max((_cell_width(k) for k, _ in rows), default=0)
        if title:
            print(title, file=stream)
        for k, v in rows:
            line = f"  {_pad(k, key_w, 'left')}  {v}".rstrip()
            print(_truncate(line, width) if width else line, file=stream)
        return

    color = _use_color(stream)
    table = rich.Table(title=title, show_header=False, box=None, padding=(0, 2))
    table.add_column("key", style="dim" if color else "")
    table.add_column("value", overflow="fold")
    for k, v in rows:
        table.add_row(rich.Text(k), rich.Text(v))
    _console(rich, stream, width).print(table)


# =============================================================================
# Format dispatch
# =============================================================================


def render(
    rows: Sequence[Row],
    columns: Sequence[ColumnSpec],
    fmt: OutputFormat | str = OutputFormat.TABLE,
    *,
    title: str | None = None,
    out: TextIO | None = None,
    width: int | None = None,
) -> None:
    """Render ``rows`` in ``fmt`` (``table`` | ``plain`` | ``json`` | ``csv``)."""
    fmt = OutputFormat.parse(fmt)
    stream = out if out is not None else sys.stdout
    if fmt in (OutputFormat.TABLE, OutputFormat.PLAIN):
        render_table(
            rows,
            columns,
            title=title,
            out=stream,
            width=width,
            plain=fmt is OutputFormat.PLAIN,
        )
        return

    cols = _columns(columns)
    records = [{c.key: _raw(r, i, c.key) for i, c in enumerate(cols)} for r in rows]
    if fmt is OutputFormat.JSON:
        print(json.dumps(records, indent=2, default=str), file=stream)
        return
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow([c.key for c in cols])
    for rec in records:
        writer.writerow(["" if rec[c.key] is None else rec[c.key] for c in cols])
