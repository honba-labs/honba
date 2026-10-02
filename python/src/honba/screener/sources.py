"""Local in-memory / Parquet catalog ScreenerSource (Design.md Section 7 & 14.3)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from honba.entities.instrument import InstrumentId
from honba.entities.screener import (
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
    ScreenerRow,
    ScreenerScanRequest,
    ScreenerScanResponse,
)
from honba.markets.india.calendar import NseCalendar
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import DateInterval
from honba.screener.evaluator import evaluate_group_on_bars, extract_metrics_from_bars
from honba.screener.ports import ScreenerSource
from honba.screener.presets import get_lookback_bars
from honba.screener.service import DataService


def _gather_metric_keys(req: ScreenerScanRequest) -> set[str]:
    """Extract all metric keys referenced in columns, sort, and filters."""
    keys: set[str] = set()
    for col in req.columns:
        keys.add(col.key)
    if req.sort:
        keys.add(req.sort.key)

    def walk_group(group: ScreenerFilterGroup | None) -> None:
        if not group:
            return
        for item in group.items:
            if isinstance(item, ScreenerFilterGroup):
                walk_group(item)
            elif isinstance(item, ScreenerFilterPredicate):
                keys.add(item.key)
            elif isinstance(item, dict):
                if "operator" in item:
                    walk_group(ScreenerFilterGroup.model_validate(item))
                elif "key" in item:
                    keys.add(item["key"])

    walk_group(req.filters)
    return keys


class LocalScreenerSource:
    """Evaluates screener scan requests against BarStore / DataService."""

    def __init__(
        self,
        data_service: DataService,
        calendar: NseCalendar | None = None,
        default_instruments: Sequence[InstrumentId] | None = None,
    ) -> None:
        self.data_service = data_service
        self.calendar = calendar or NseCalendar()
        self.default_instruments = list(default_instruments) if default_instruments is not None else None

    def scan(
        self, request: ScreenerScanRequest, asof: dt.date | None = None
    ) -> ScreenerScanResponse:
        # 1. Resolve instruments
        instruments: list[InstrumentId] = []
        if self.default_instruments is not None:
            instruments = list(self.default_instruments)
        elif request.market.lower() == "india":
            instruments = resolve_universe("nifty50", venue="NSE")
        else:
            instruments = []

        # 2. Compute max lookback across all referenced metrics
        all_metric_keys = _gather_metric_keys(request)
        max_lookback = max((get_lookback_bars(k) for k in all_metric_keys), default=1)

        # 3. Determine required date interval
        # For historical or latest scans, default asof to today
        eval_asof = asof or dt.date.today()
        # Find session start date for max_lookback sessions before asof
        sessions = self.calendar.sessions_before(eval_asof, max_lookback + 5)
        start_date = sessions[0] if sessions else eval_asof - dt.timedelta(days=max_lookback * 2)
        end_date = eval_asof + dt.timedelta(days=1)
        required_interval = DateInterval(start_date, end_date)

        timeframe = "1D"
        if request.sort and request.sort.timeframe:
            timeframe = request.sort.timeframe.value

        # 4. DataService.ensure to guarantee bar data
        plan = self.data_service.plan(instruments, timeframe, required_interval)
        ensure_res = self.data_service.ensure(plan)

        # 5. Evaluate filters per instrument
        matching_rows: list[ScreenerRow] = []
        for inst in instruments:
            bars = ensure_res.bars.get(inst, [])
            if not bars:
                continue

            # Evaluate filters
            if request.filters:
                passed = evaluate_group_on_bars(request.filters, bars)
                if not passed:
                    continue

            # Extract column values
            col_keys = [c.key for c in request.columns] if request.columns else ["close", "volume"]
            values = extract_metrics_from_bars(col_keys, bars)

            matching_rows.append(
                ScreenerRow(
                    full_symbol=f"{inst.symbol}.{inst.venue}",
                    instrument_id=str(inst),
                    name=inst.symbol,
                    values=values,
                )
            )

        # 6. Sorting
        if request.sort:
            sort_key = request.sort.key
            reverse = request.sort.dir == "desc"

            def get_sort_val(r: ScreenerRow) -> Any:
                val = r.values.get(sort_key)
                return -float("inf") if val is None else val

            matching_rows.sort(key=get_sort_val, reverse=reverse)

        # 7. Pagination
        offset, limit = request.range
        paged_rows = matching_rows[offset : offset + limit]

        col_names = [c.key for c in request.columns] if request.columns else ["close", "volume"]

        return ScreenerScanResponse(
            total=len(matching_rows),
            range=request.range,
            columns=col_names,
            rows=paged_rows,
        )
