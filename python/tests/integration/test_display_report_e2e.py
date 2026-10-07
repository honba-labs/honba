"""End-to-end: a real ``BacktestResult`` through ``print_backtest_report`` in each format.

Snapshots live in ``tests/fixtures/display/``. Output is deterministic (fixed width, no colour,
fixed timestamps). To refresh after an intended layout change run with ``HONBA_BLESS=1``.
"""

from __future__ import annotations

import builtins
import csv
import io
import json
import os
import sys
from pathlib import Path

import pytest

from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderSide
from honba.entities.trade import Trade
from honba.report import print_backtest_report
from honba.session import BacktestConfig, BacktestResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "display"
WIDTH = 72
DAY_NS = 86_400 * 10**9
T0 = 1_704_153_600 * 10**9  # 2024-01-02 00:00:00 UTC


def _result() -> BacktestResult:
    inst = InstrumentId("RELIANCE", "NSE")
    fills = [
        Trade(
            inst,
            OrderSide.BUY if i % 2 == 0 else OrderSide.SELL,
            10 + i,
            2400.0 + 12.5 * i,
            T0 + i * DAY_NS,
            f"o{i}",
            costs=1.25 + i,
        )
        for i in range(12)
    ]
    return BacktestResult(
        strategy_name="sma_crossover",
        config=BacktestConfig(
            symbol="RELIANCE",
            start="2024-01-01",
            end="2024-06-30",
            timeframe="1d",
            cash=1_000_000.0,
        ),
        fills=fills,
        metrics={
            "final_equity": 1_084_250.5,
            "final_cash": 412_000.0,
            "total_return_pct": 8.425,
            "max_drawdown_pct": -3.2,
            "n_trades": 12,
            "n_fills": 12,
            "sharpe": 1.4567,
        },
        notes=["warm-up: 20 bars", "costs: india.equity"],
    )


def _render(fmt: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = io.StringIO()
    print_backtest_report(_result(), format=fmt, file=out, width=WIDTH)
    return out.getvalue()


def _check(name: str, text: str) -> None:
    path = FIXTURES / name
    if os.environ.get("HONBA_BLESS") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    ("fmt", "snapshot"),
    [
        ("table", "report_table.txt"),
        ("plain", "report_plain.txt"),
        ("csv", "report_fills.csv"),
        ("json", "report.json"),
    ],
)
def test_report_matches_snapshot(fmt: str, snapshot: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _check(snapshot, _render(fmt, monkeypatch))


def test_table_carries_all_report_information(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _render("table", monkeypatch)
    for needle in (
        "sma_crossover",
        "RELIANCE.NSE",
        "2024-01-01 → 2024-06-30",
        "Initial cash",
        "Final Equity",
        "+8.43%",
        "Recent fills (last 10)",
        "2024-01-12",
        "BUY",
        "SELL",
        "warm-up: 20 bars",
        "costs: india.equity",
    ):
        assert needle in text
    assert "\x1b[" not in text
    assert all(len(line) <= WIDTH for line in text.splitlines())


def test_table_shows_only_the_last_ten_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _render("table", monkeypatch)
    assert "2024-01-02" not in text.split("Recent fills")[1]
    assert "2024-01-13" in text


def test_table_without_rich_equals_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _render("plain", monkeypatch)
    for name in [m for m in sys.modules if m == "rich" or m.startswith("rich.")]:
        monkeypatch.delitem(sys.modules, name)
    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "rich" or name.startswith("rich."):
            raise ImportError("rich blocked")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert _render("table", monkeypatch) == plain


def test_csv_is_machine_readable_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = list(csv.DictReader(io.StringIO(_render("csv", monkeypatch))))
    assert len(rows) == 12
    assert list(rows[0]) == ["ts", "side", "symbol", "quantity", "price", "costs"]
    assert rows[0]["side"] == "BUY" and rows[0]["symbol"] == "RELIANCE.NSE"


def test_json_format_is_unchanged_by_display_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.loads(_render("json", monkeypatch))
    assert data["strategy"] == "sma_crossover" and data["n_fills"] == 12
