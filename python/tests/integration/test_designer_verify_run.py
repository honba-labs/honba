"""``verify_and_run`` on the golden crossover graph: verify, generate, and run (E9-S4).

Skips cleanly when the native extension is unavailable (manifest compilation needs it).
No network, no wall clock: ``verify_and_run`` is deterministic for fixed inputs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.designer import verify_and_run
from honba.strategies.graph import StrategyGraph

pytest.importorskip("honba._honba")

X = InstrumentId("TCS", "NSE")
DAY = 86_400 * 10**9
T0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9


def golden() -> StrategyGraph:
    return StrategyGraph.build(
        "sma_cross",
        universe=[X],
        driving_timeframe=(1, "day"),
        warmup_bars=2,
        nodes=[
            {"id": "n1", "type": "universe", "label": "Universe"},
            {"id": "f", "type": "indicator", "indicator": "sma", "params": {"period": 2}},
            {"id": "s", "type": "indicator", "indicator": "sma", "params": {"period": 4}},
            {"id": "c", "type": "condition", "params": {"op": "crosses_above"}},
            {
                "id": "o",
                "type": "order",
                "label": "Entry Long",
                "params": {"side": "buy", "quantity": 1},
            },
        ],
        edges=[
            {
                "id": "e1",
                "from_node": "n1",
                "from_port": "bars",
                "to_node": "f",
                "to_port": "input",
            },
            {
                "id": "e2",
                "from_node": "n1",
                "from_port": "bars",
                "to_node": "s",
                "to_port": "input",
            },
            {"id": "e3", "from_node": "f", "from_port": "value", "to_node": "c", "to_port": "left"},
            {
                "id": "e4",
                "from_node": "s",
                "from_port": "value",
                "to_node": "c",
                "to_port": "right",
            },
            {
                "id": "e5",
                "from_node": "c",
                "from_port": "signal",
                "to_node": "o",
                "to_port": "signal",
            },
        ],
    )


def trending_bars() -> list[Bar]:
    closes = [
        100,
        101,
        103,
        102,
        101,
        100,
        99,
        98,
        97,
        99,
        101,
        104,
        108,
        112,
        110,
        108,
        106,
        104,
        102,
        100,
        99,
        101,
        105,
        110,
    ]
    return [
        Bar(X, T0 + i * DAY, closes[i], closes[i] + 1, closes[i] - 1, closes[i], 1_000.0)
        for i in range(len(closes))
    ]


def test_verify_and_run_returns_the_ir_and_a_completed_run() -> None:
    outcome = verify_and_run(
        golden(),
        bars=trending_bars(),
        symbol="TCS",
        start="2024-01-01",
        end="2024-02-01",
        timeframe="1d",
        costs="none",
    )
    assert outcome.verify.ok
    assert outcome.verify.ir is not None
    assert outcome.verify.ir["manifest"]["name"] == "sma_cross"
    assert outcome.run is not None
    assert outcome.run.graph_hash == golden().graph_hash()
    result = outcome.run.result
    assert len(result.fills) >= 1
    assert result.metrics["n_fills"] == float(len(result.fills))
    assert len(result.equity_curve) == len(trending_bars())
    # determinism: the same graph and inputs give the same handle
    again = verify_and_run(
        golden(),
        bars=trending_bars(),
        symbol="TCS",
        start="2024-01-01",
        end="2024-02-01",
        timeframe="1d",
        costs="none",
    )
    assert again.run is not None
    assert again.run.handle == outcome.run.handle
    assert again.run.result.fills == result.fills


def test_an_invalid_graph_returns_issues_and_no_run() -> None:
    broken = golden().model_copy(update={"name": "  "})
    outcome = verify_and_run(
        broken, bars=trending_bars(), symbol="TCS", start="2024-01-01", end="2024-02-01"
    )
    assert not outcome.verify.ok
    assert outcome.verify.issues and outcome.verify.issues[0].code == "empty_name"
    assert outcome.run is None
