"""Goldens and determinism for graph -> code generation (E9-S2)."""

from __future__ import annotations

from honba.entities.instrument import InstrumentId
from honba.strategies.graph import StrategyGraph
from honba.strategies.graph_codegen import CLASS_MARKER, MARKER, generate

X = InstrumentId("TCS", "NSE")


def crossover() -> StrategyGraph:
    return StrategyGraph.build(
        "sma_cross",
        universe=[X],
        driving_timeframe=(1, "minute"),
        warmup_bars=2,
        nodes=[
            {"id": "n1", "type": "universe", "label": "Universe"},
            {
                "id": "f",
                "type": "indicator",
                "indicator": "sma",
                "label": "Fast SMA",
                "params": {"period": 2},
            },
            {
                "id": "s",
                "type": "indicator",
                "indicator": "sma",
                "label": "Slow SMA",
                "params": {"period": 4},
            },
            {"id": "c", "type": "condition", "label": "Cross", "params": {"op": "crosses_above"}},
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


def test_generation_is_deterministic() -> None:
    assert generate(crossover()) == generate(crossover())
    assert generate(crossover()).endswith("\n")


def test_golden_markers_params_and_reason_strings() -> None:
    source = generate(crossover())
    lines = source.splitlines()
    assert MARKER in lines
    assert CLASS_MARKER in lines
    assert "GRAPH_HASH = " in source
    assert f"GRAPH_HASH = {crossover().graph_hash()!r}" in source
    # builder block is lossless and stable
    assert (
        ".node(id='f', type='indicator', label='Fast SMA', indicator='sma', params={'period': 2})"
        in source
    )
    assert (
        ".edge(id='e5', from_node='c', from_port='signal', to_node='o', to_port='signal')" in source
    )
    # params are one-line declarations
    assert "f_period = Parameter(default=2, description='period')" in source
    assert "s_period = Parameter(default=4, description='period')" in source
    # reason strings carry the order node's label
    assert "reason='Entry Long'" in source
    # generated class is marked so it is part of the recognised subset
    assert lines[lines.index(CLASS_MARKER) + 1].startswith("class SmaCross(Strategy):")


def test_the_generated_source_is_valid_python_and_defines_a_strategy() -> None:
    namespace: dict = {}
    exec(compile(generate(crossover()), "<gen>", "exec"), namespace)  # noqa: S102 - generated code
    klass = namespace["SmaCross"]
    assert klass.name == "sma_cross"
    assert set(klass.hyperparameters()) == {"f_period", "s_period"}


def test_a_graph_of_only_a_universe_still_generates_parseable_code() -> None:
    graph = StrategyGraph.build(
        "empty_pipeline",
        universe=[X],
        driving_timeframe=(1, "day"),
        nodes=[{"id": "n1", "type": "universe"}],
    )
    source = generate(graph)
    compile(source, "<gen>", "exec")
    assert "def on_bar(self, bar) -> None:" in source
