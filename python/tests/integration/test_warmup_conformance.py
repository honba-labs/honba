"""Cross-language vectors for the warm-up gate and the strategy manifest.

Reads ``schema/conformance/warmup_gate.json`` and ``schema/conformance/strategy_manifest.json``;
the Rust tests in ``crates/honba-strategy/tests/warmup.rs`` read the same files.
The warm-up scenarios run through the real ``StrategyRunner`` and ``LedgerContext``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pydantic
import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.entities.tick import QuoteTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.manifest import ManifestError, StrategyManifest
from honba.strategies.runner import StrategyRunner

CONFORMANCE = Path(__file__).resolve().parents[3] / "schema" / "conformance"
WARMUP = json.loads((CONFORMANCE / "warmup_gate.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((CONFORMANCE / "strategy_manifest.json").read_text(encoding="utf-8"))


def _iid(symbol: str) -> InstrumentId:
    return InstrumentId(symbol, "NSE")


class WarmupProbe(Strategy):
    """Buys 1 of the first instrument on start and 1 of the bar's instrument on every bar."""

    name = WARMUP["strategy_name"]

    def __init__(self, first: str) -> None:
        self.first = first

    def on_start(self) -> None:
        self.ctx.submit(OrderIntent.market_buy(_iid(self.first), 1))

    def on_bar(self, bar: Bar) -> None:
        self.ctx.submit(OrderIntent.market_buy(bar.instrument_id, 1))


class NeverFills:
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def drain_fills(self) -> list[Trade]:
        return []


def _event(kind: str, symbol: str, ts: int) -> Any:
    if kind == "bar":
        return Bar(_iid(symbol), ts, 1.0, 1.0, 1.0, 1.0, 1.0)
    assert kind == "quote", kind
    return QuoteTick(_iid(symbol), ts, 1.0, 1.1, 1.0, 1.0)


@pytest.mark.parametrize("scenario", WARMUP["scenarios"], ids=lambda s: s["name"])
def test_warmup_gate_vectors(scenario: dict[str, Any]) -> None:
    events = [(_event(k, sym, ts), ts) for k, sym, ts in scenario["events"]]
    runner = StrategyRunner(
        WarmupProbe(scenario["events"][0][1]), NeverFills(), warmup_bars=scenario["warmup_bars"]
    )
    result = runner.run(events)
    assert [[s.ts_init, s.intent.instrument_id.symbol] for s in result.suppressed] == scenario[
        "suppressed"
    ]
    assert [
        [s.ts_init, s.order_id, s.intent.instrument_id.symbol] for s in result.intents
    ] == scenario["submitted"]
    # Suppressed intents are released: only submitted (never filled) orders stay busy.
    busy = {sym for _, _, sym in scenario["submitted"]}
    for _, sym, _ in scenario["events"]:
        assert result.ctx.busy(_iid(sym)) == (sym in busy)


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c: c["name"])
def test_manifest_vectors(case: dict[str, Any]) -> None:
    if case["error"] == "deserialize":
        with pytest.raises(pydantic.ValidationError):
            StrategyManifest.model_validate(case["value"])
        return
    manifest = StrategyManifest.model_validate(case["value"])
    if case["error"] is None:
        manifest.validate_manifest()
        # Serializes to the Rust shape: defaults filled in, empty schedules omitted.
        expected = json.loads(json.dumps(case["value"]))
        expected["subscriptions"] = {"quotes": False, "trades": False, **expected["subscriptions"]}
        if not expected.get("schedules"):
            expected.pop("schedules", None)
        assert manifest.to_json_dict() == expected
    else:
        with pytest.raises(ManifestError) as err:
            manifest.validate_manifest()
        assert err.value.code == case["error"]
