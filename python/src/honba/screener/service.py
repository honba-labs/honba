"""Application service DataService orchestrating gap filling and data assurance (Design.md Section 12)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.screener.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
    plan_gaps,
)
from honba.screener.ports import BarStore, MarketDataProvider, validate_bar


class MissingDataPolicy(Enum):
    AUTO = "auto"
    NEVER = "never"
    FORCE = "force"


class OnMissingAction(Enum):
    ERROR = "error"
    SKIP = "skip"
    WARN = "warn"


@dataclass(frozen=True)
class GapFetchPlan:
    """Gap fetch plan for a set of instruments and timeframe."""

    timeframe: str
    required_interval: DateInterval
    # Map instrument -> missing date intervals
    gaps_by_instrument: dict[InstrumentId, list[DateInterval]]

    @property
    def total_gaps(self) -> int:
        return sum(len(gaps) for gaps in self.gaps_by_instrument.values())


@dataclass
class DataEnsureResult:
    """Result of DataService.ensure."""

    success: bool
    bars: dict[InstrumentId, list[Bar]] = field(default_factory=dict)
    unfilled_gaps: dict[InstrumentId, list[DateInterval]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class DataService:
    """Application service that coordinates gap planning, provider fetching, validation, and storage."""

    def __init__(
        self,
        store: BarStore,
        providers: Sequence[MarketDataProvider],
        policy: MissingDataPolicy = MissingDataPolicy.AUTO,
        on_missing: OnMissingAction = OnMissingAction.WARN,
    ) -> None:
        self.store = store
        self.providers = list(providers)
        self.policy = policy
        self.on_missing = on_missing

    def plan(
        self,
        instruments: Sequence[InstrumentId],
        timeframe: str,
        required_interval: DateInterval,
        max_gap_days: int = 2,
    ) -> GapFetchPlan:
        """Compute the gap fetch plan for the given instruments."""
        gaps_map: dict[InstrumentId, list[DateInterval]] = {}
        for inst in instruments:
            if self.policy == MissingDataPolicy.FORCE:
                gaps_map[inst] = [required_interval]
                continue

            records = self.store.coverage(inst, timeframe)
            # Filter out empty records if needed or keep final/provisional
            covered = [r.interval for r in records if r.status in (CoverageStatus.FINAL, CoverageStatus.PROVISIONAL)]
            gaps = plan_gaps(required_interval, covered, max_gap_days=max_gap_days)
            if gaps:
                gaps_map[inst] = gaps
            else:
                gaps_map[inst] = []

        return GapFetchPlan(
            timeframe=timeframe,
            required_interval=required_interval,
            gaps_by_instrument=gaps_map,
        )

    def ensure(self, plan: GapFetchPlan, progress_callback: Any = None) -> DataEnsureResult:
        """Fetch all missing gaps in the plan, validate bars, persist to store, and return read view."""
        if self.policy == MissingDataPolicy.NEVER and plan.total_gaps > 0:
            msg = f"Missing data for {plan.total_gaps} gaps with --fetch never"
            if self.on_missing == OnMissingAction.ERROR:
                raise RuntimeError(msg)
            return DataEnsureResult(success=False, warnings=[msg])

        unfilled: dict[InstrumentId, list[DateInterval]] = {}

        # Fetch gaps
        for inst, gaps in plan.gaps_by_instrument.items():
            for gap in gaps:
                fetched_bars: list[Bar] = []
                fetched = False
                source_used = "unknown"

                for provider in self.providers:
                    try:
                        bars = provider.fetch(inst, plan.timeframe, gap, progress_callback=progress_callback)
                        if not bars:
                            continue
                        source_used = provider.name
                        # Validate
                        for b in bars:
                            validate_bar(b)
                        fetched_bars = bars
                        fetched = True
                        break
                    except Exception:
                        continue

                if fetched:
                    record = CoverageRecord(
                        venue=inst.venue,
                        symbol=inst.symbol,
                        timeframe=plan.timeframe,
                        interval=gap,
                        status=CoverageStatus.FINAL if fetched_bars else CoverageStatus.EMPTY,
                        source=source_used,
                        row_count=len(fetched_bars),
                        fetched_at_ns=int(dt.datetime.now().timestamp() * 1e9),
                    )
                    self.store.append(record, fetched_bars)
                else:
                    unfilled.setdefault(inst, []).append(gap)

        # Assemble read view
        result_bars: dict[InstrumentId, list[Bar]] = {}
        for inst in plan.gaps_by_instrument:
            bars = self.store.read(inst, plan.timeframe, plan.required_interval)
            result_bars[inst] = bars

        success = len(unfilled) == 0
        warnings: list[str] = []
        if unfilled:
            msg = f"Failed to fill gaps for {len(unfilled)} instrument(s)"
            warnings.append(msg)
            if self.on_missing == OnMissingAction.ERROR:
                raise RuntimeError(msg)

        return DataEnsureResult(
            success=success,
            bars=result_bars,
            unfilled_gaps=unfilled,
            warnings=warnings,
        )
