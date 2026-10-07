"""Unit tests for honba.display: shared Rich-based table / key-value rendering."""

from __future__ import annotations

import builtins
import csv
import io
import json
import sys

import pytest

from honba.display import (
    Column,
    OutputFormat,
    format_money,
    format_percent,
    format_timestamp_ns,
    render,
    render_kv,
    render_table,
)

ROWS = [
    {"symbol": "RELIANCE", "qty": 10, "price": 2450.5},
    {"symbol": "TCS", "qty": 125, "price": 3900.0},
]
COLS = [
    Column("symbol", "Symbol"),
    Column("qty", "Qty"),
    Column("price", "Price", fmt=lambda v: f"{v:.2f}"),
]


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _block_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "rich" or m.startswith("rich.")]:
        monkeypatch.delitem(sys.modules, name)
    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "rich" or name.startswith("rich."):
            raise ImportError("rich blocked")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


# ---- Rich layout --------------------------------------------------------------------------------


def test_table_contains_headers_title_and_values() -> None:
    out = io.StringIO()
    render_table(ROWS, COLS, title="Positions", out=out, width=60)
    text = out.getvalue()
    for needle in ("Positions", "Symbol", "Qty", "Price", "RELIANCE", "TCS", "2450.50", "3900.00"):
        assert needle in text


def test_numeric_columns_are_right_aligned_and_text_left_aligned() -> None:
    out = io.StringIO()
    render_table(ROWS, COLS, out=out, width=60, plain=True)
    lines = _lines(out.getvalue())
    header, _sep, first, second = lines[0], lines[1], lines[2], lines[3]
    assert first.startswith("RELIANCE")
    # right-aligned: the narrower number ends in the same column as the wider one
    assert first.index("10") + 2 == second.index("125") + 3
    assert header.rstrip().endswith("Price")
    assert first.rstrip().endswith("2450.50")


def test_rich_and_plain_share_columns_and_values() -> None:
    rich_out, plain_out = io.StringIO(), io.StringIO()
    render_table(ROWS, COLS, out=rich_out, width=60)
    render_table(ROWS, COLS, out=plain_out, width=60, plain=True)
    for token in ("Symbol", "Qty", "Price", "RELIANCE", "125", "3900.00"):
        assert token in rich_out.getvalue()
        assert token in plain_out.getvalue()


def test_output_is_deterministic_for_a_fixed_width() -> None:
    a, b = io.StringIO(), io.StringIO()
    render_table(ROWS, COLS, title="T", out=a, width=50)
    render_table(ROWS, COLS, title="T", out=b, width=50)
    assert a.getvalue() == b.getvalue()


def test_wide_content_is_confined_to_the_requested_width() -> None:
    rows = [{"name": "x" * 80, "v": 1}]
    for plain in (False, True):
        out = io.StringIO()
        render_table(rows, ["name", "v"], out=out, width=40, plain=plain)
        assert all(len(ln) <= 40 for ln in out.getvalue().splitlines())


def test_plain_truncates_with_an_ellipsis_when_too_wide() -> None:
    out = io.StringIO()
    render_table([{"name": "y" * 80}], ["name"], out=out, width=20, plain=True)
    assert "…" in out.getvalue()


def test_column_max_width_truncates_in_plain() -> None:
    out = io.StringIO()
    render_table(
        [{"name": "abcdefghij"}], [Column("name", max_width=5)], out=out, width=80, plain=True
    )
    assert "abcd…" in out.getvalue()
    assert "abcdefghij" not in out.getvalue()


def test_unicode_wide_characters_align_by_display_width() -> None:
    rows = [{"k": "日本", "v": 1}, {"k": "ab", "v": 22}]
    out = io.StringIO()
    render_table(rows, ["k", "v"], out=out, width=40, plain=True)
    lines = _lines(out.getvalue())[2:4]

    def cells(text: str) -> int:
        return sum(2 if ord(c) >= 0x2E80 else 1 for c in text)

    # Every line spans the same number of terminal cells (wide glyphs count twice).
    assert cells(lines[0]) == cells(lines[1])
    assert lines[0].endswith("1") and lines[1].endswith("22")


def test_empty_rows_still_render_headers() -> None:
    for plain in (False, True):
        out = io.StringIO()
        render_table([], COLS, title="Empty", out=out, width=50, plain=plain)
        text = out.getvalue()
        assert "Symbol" in text and "Price" in text and "Empty" in text


def test_none_cells_render_as_a_dash() -> None:
    out = io.StringIO()
    render_table([{"a": None, "b": 1}], ["a", "b"], out=out, width=30, plain=True)
    assert "-" in _lines(out.getvalue())[2]


def test_sequence_rows_are_positional() -> None:
    out = io.StringIO()
    render_table([("A", 1), ("B", 2)], ["x", "y"], out=out, width=30, plain=True)
    assert "A" in out.getvalue() and "B" in out.getvalue()


def test_markup_like_text_is_not_interpreted() -> None:
    out = io.StringIO()
    render_table([{"a": "[bold]hi[/bold]"}], ["a"], out=out, width=30)
    assert "[bold]hi[/bold]" in out.getvalue()


# ---- colour -------------------------------------------------------------------------------------


def test_no_ansi_when_output_is_not_a_tty() -> None:
    out = io.StringIO()
    render_table(ROWS, [Column("symbol", style="cyan"), "qty"], title="T", out=out, width=40)
    render_kv({"a": 1}, title="T", out=out)
    assert "\x1b[" not in out.getvalue()


def test_ansi_is_emitted_on_a_tty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    out = _Tty()
    render_table(ROWS, [Column("symbol", style="cyan"), "qty"], title="T", out=out, width=40)
    assert "\x1b[" in out.getvalue()


def test_no_color_env_suppresses_ansi_on_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    out = _Tty()
    render_table(ROWS, [Column("symbol", style="cyan"), "qty"], title="T", out=out, width=40)
    render_kv({"a": 1}, out=out)
    assert "\x1b[" not in out.getvalue()
    assert "RELIANCE" in out.getvalue()


# ---- plain fallback -----------------------------------------------------------------------------


def test_falls_back_to_plain_text_when_rich_is_not_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_rich(monkeypatch)
    out = io.StringIO()
    render_table(ROWS, COLS, title="Positions", out=out, width=60)
    render_kv({"Strategy": "sma", "Cash": "1,000"}, title="Meta", out=out)
    text = out.getvalue()
    assert "Positions" in text and "RELIANCE" in text and "2450.50" in text
    assert "Strategy" in text and "sma" in text
    assert "┏" not in text and "╭" not in text


# ---- kv -----------------------------------------------------------------------------------------


def test_kv_renders_pairs_in_order_with_title() -> None:
    out = io.StringIO()
    render_kv([("Strategy", "sma"), ("Cash", 1000)], title="Meta", out=out)
    text = out.getvalue()
    assert text.index("Meta") < text.index("Strategy") < text.index("Cash")
    assert "sma" in text and "1000" in text


def test_kv_accepts_mappings_and_aligns_values() -> None:
    out = io.StringIO()
    render_kv({"a": 1, "long_key": 2}, out=out, plain=True)
    first, second = _lines(out.getvalue())
    assert first.index("1") == second.index("2")


# ---- machine formats ----------------------------------------------------------------------------


def test_json_is_a_list_of_objects_in_column_order_with_raw_values() -> None:
    out = io.StringIO()
    render(ROWS, COLS, OutputFormat.JSON, out=out)
    data = json.loads(out.getvalue())
    assert data == ROWS
    assert list(data[0]) == ["symbol", "qty", "price"]
    assert data[0]["price"] == 2450.5


def test_json_of_empty_rows_is_an_empty_list() -> None:
    out = io.StringIO()
    render([], COLS, "json", out=out)
    assert json.loads(out.getvalue()) == []


def test_csv_has_header_row_and_stable_column_order() -> None:
    out = io.StringIO()
    render(ROWS, COLS, "csv", out=out)
    parsed = list(csv.reader(io.StringIO(out.getvalue())))
    assert parsed[0] == ["symbol", "qty", "price"]
    assert parsed[1] == ["RELIANCE", "10", "2450.5"]
    assert len(parsed) == 3


def test_csv_of_empty_rows_is_the_header_only() -> None:
    out = io.StringIO()
    render([], COLS, "csv", out=out)
    assert out.getvalue().strip() == "symbol,qty,price"


def test_csv_quotes_commas_and_json_stringifies_unknown_types() -> None:
    out = io.StringIO()
    render([{"a": "x,y", "b": 2}], ["a", "b"], "csv", out=out)
    assert '"x,y"' in out.getvalue()
    out = io.StringIO()
    render([{"a": object}], ["a"], "json", out=out)
    assert isinstance(json.loads(out.getvalue())[0]["a"], str)


def test_missing_keys_are_null_in_json_and_empty_in_csv() -> None:
    j, c = io.StringIO(), io.StringIO()
    render([{"a": 1}], ["a", "b"], "json", out=j)
    render([{"a": 1}], ["a", "b"], "csv", out=c)
    assert json.loads(j.getvalue()) == [{"a": 1, "b": None}]
    assert c.getvalue().splitlines()[1] == "1,"


def test_render_table_format_uses_the_table_renderer_and_plain_is_ascii_aligned() -> None:
    t, p = io.StringIO(), io.StringIO()
    render(ROWS, COLS, "table", out=t, width=60)
    render(ROWS, COLS, "plain", out=p, width=60)
    assert "RELIANCE" in t.getvalue() and "RELIANCE" in p.getvalue()
    assert "┃" not in p.getvalue()


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown output format"):
        render(ROWS, COLS, "xml", out=io.StringIO())


def test_output_format_parses_case_insensitively() -> None:
    assert OutputFormat.parse(" JSON ") is OutputFormat.JSON
    assert [f.value for f in OutputFormat] == ["table", "json", "csv", "plain"]


# ---- cell helpers -------------------------------------------------------------------------------


def test_format_money_uses_the_inr_convention() -> None:
    assert format_money(1500.0).startswith("₹")


def test_format_percent_is_signed_with_two_decimals() -> None:
    assert format_percent(12.5) == "+12.50%"
    assert format_percent(-1) == "-1.00%"


def test_format_timestamp_ns_is_a_utc_date_or_question_mark() -> None:
    assert format_timestamp_ns(1_700_000_000 * 10**9) == "2023-11-14"
    assert format_timestamp_ns(0) == "?"
