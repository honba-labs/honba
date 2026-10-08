"""Helpers shared by the risk-in-a-run integration tests (JSON in, JSON out)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

X = {"symbol": "X", "exchange": "NSE"}
INSTRUMENT = {
    "instrument_id": X,
    "kind": "equity",
    "currency": "INR",
    "lot_size": 1.0,
    "tick_size": 0.05,
}


def ts(ns: int) -> dict[str, str]:
    sec, rem = divmod(ns, 10**9)
    iso = datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return {"iso": f"{iso}.{rem:09d}Z", "unix_nanos": str(ns)}


def bar(close: float, ns: int) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "event": {
            "type": "bar",
            "bar_type": {
                "instrument_id": X,
                "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
            },
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
            "ts_event": ts(ns),
            "ts_init": ts(ns),
        },
        "ts_init": ts(ns),
    }


def order(at_bar: int, side: str, quantity: float) -> dict[str, Any]:
    return {"bar": at_bar, "instrument_id": X, "side": side, "quantity": quantity}


def run(native: Any, orders: list[dict[str, Any]], bars: list[dict[str, Any]], risk: Any) -> Any:
    out = native.run_strategy(
        "scripted_orders",
        json.dumps({"orders": orders}),
        json.dumps(bars),
        json.dumps([INSTRUMENT]),
        1_000_000.0,
        risk=risk,
    )
    return json.loads(out)
