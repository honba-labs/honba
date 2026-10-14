"""Unit tests for the report builder fact snapshot and quality gate (E5-S9)."""

from __future__ import annotations

import pytest

from honba.ai.report import FactSnapshot, build_report, quality_gate


def test_snapshot_is_immutable() -> None:
    metrics = {"total_return_pct": 12.5}
    snapshot = FactSnapshot(run_id="run-1", strategy="sma", metrics=metrics)

    metrics["total_return_pct"] = 99.0
    assert snapshot.metrics["total_return_pct"] == 12.5

    with pytest.raises(TypeError):
        snapshot.metrics["total_return_pct"] = 1.0  # type: ignore[index]


def test_narrator_receives_only_the_snapshot() -> None:
    snapshot = FactSnapshot(run_id="run-2", strategy="sma", metrics={"total_return_pct": 12.5})
    calls: list[tuple[object, ...]] = []

    def narrator(*args: object) -> str:
        calls.append(args)
        return "Return 12.5%."

    build_report(snapshot, narrator)

    assert len(calls) == 1
    assert calls[0] == (snapshot,)


def test_quality_gate_allows_snapshot_numbers() -> None:
    snapshot = FactSnapshot(
        run_id="run-3",
        strategy="sma",
        metrics={"total_return_pct": 12.5, "max_drawdown_pct": 8.2},
    )

    report = quality_gate("Return 12.5% with drawdown 8.2%.", snapshot)

    assert report.ok is True
    assert report.violations == ()


def test_narrative_number_outside_snapshot_rejected() -> None:
    snapshot = FactSnapshot(
        run_id="run-4",
        strategy="sma",
        metrics={"total_return_pct": 12.5, "max_drawdown_pct": 8.2},
    )

    report = quality_gate("Return 99.9%.", snapshot)

    assert report.ok is False
    assert any("99.9" in violation.number for violation in report.violations)


def test_percentage_and_currency_forms_are_matched() -> None:
    currency = FactSnapshot(run_id="run-5", strategy="sma", metrics={"pnl": 1234.56})
    assert quality_gate("P&L of ₹1,234.56", currency).ok is True

    percent = FactSnapshot(run_id="run-6", strategy="sma", metrics={"ret": 12.5})
    assert quality_gate("Return 12.5%", percent).ok is True
