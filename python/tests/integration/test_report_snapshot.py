"""Integration tests for the report builder over a run result (E5-S9)."""

from __future__ import annotations

from honba.ai.report import build_report, build_snapshot
from honba.ai.trust import TrustEnvelope


class _StandInResult:
    """A minimal ``BacktestResult``-shaped object (no native extension required)."""

    def __init__(self) -> None:
        self.strategy_name = "sma_crossover"
        self.metrics = {"total_return_pct": 12.5, "max_drawdown_pct": 8.2}
        self.run_id = "run-2024-01-02"
        self.journal_ids = ("journal-7",)
        self.assumptions = ()


def test_report_uses_only_snapshot_facts() -> None:
    snapshot = build_snapshot(_StandInResult())

    good = build_report(snapshot, lambda _snapshot: "Return 12.5%")
    assert good.quality.ok is True
    assert "12.5" in good.as_markdown()

    bad = build_report(snapshot, lambda _snapshot: "Return 99.9%")
    assert bad.quality.ok is False


def test_llm_narrative_is_wrapped_untrusted() -> None:
    snapshot = build_snapshot(_StandInResult())
    report = build_report(snapshot, lambda _snapshot: "Return 12.5%")

    assert isinstance(report.untrusted, TrustEnvelope)
    assert report.untrusted.untrusted is True
    assert report.untrusted.origin == "llm:narrative"
