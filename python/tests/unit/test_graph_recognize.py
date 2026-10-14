"""Code -> graph recognition: round-trip identity over the catalogue and unsupported spans."""

from __future__ import annotations

import pytest

from honba.entities.instrument import InstrumentId
from honba.strategies.graph import StrategyGraph, indicator_catalogue
from honba.strategies.graph_codegen import generate
from honba.strategies.graph_recognize import recognize

X = InstrumentId("TCS", "NSE")


def single_indicator_graph(kind: str) -> StrategyGraph:
    params = {
        p["name"]: p["default"]
        for p in indicator_catalogue()[kind]["params"]
        if p["default"] is not None
    }
    return StrategyGraph.build(
        kind,
        universe=[X],
        driving_timeframe=(1, "minute"),
        warmup_bars=20,
        nodes=[
            {"id": "n1", "type": "universe", "label": "Universe"},
            {"id": "n2", "type": "indicator", "indicator": kind, "label": kind, "params": params},
        ],
        edges=[
            {
                "id": "e1",
                "from_node": "n1",
                "from_port": "bars",
                "to_node": "n2",
                "to_port": "input",
            }
        ],
    )


@pytest.mark.parametrize("kind", sorted(indicator_catalogue()))
def test_graph_to_code_to_graph_identity_over_the_catalogue(kind: str) -> None:
    graph = single_indicator_graph(kind)
    result = recognize(generate(graph))
    assert result.graph == graph
    assert not result.unsupported
    assert result.ok


def test_unsupported_spans_report_line_ranges() -> None:
    graph = single_indicator_graph("sma")
    source = generate(graph) + "\n\ndef helper():\n    return 1\n"
    result = recognize(source)
    assert result.graph == graph
    (span,) = result.unsupported
    start_line = source.splitlines()[span.start - 1]
    assert start_line.startswith("def helper")
    assert span.end >= span.start
    assert "helper" in span.reason


def test_hand_written_code_is_marked_code_only_and_never_lossy() -> None:
    source = (
        "from honba.strategies.base import Strategy\n"
        "class Mine(Strategy):\n"
        "    name = 'mine'\n"
        "    def on_bar(self, bar):\n"
        "        self.buy(bar.instrument_id, 1)\n"
    )
    result = recognize(source)
    assert result.graph is None
    assert result.code_only
    assert not result.ok
    assert result.unsupported and result.unsupported[0].reason


def test_syntax_errors_are_reported_as_a_span() -> None:
    result = recognize("def broken(:\n")
    assert result.graph is None and result.code_only
    assert result.unsupported[0].reason == "syntax_error"


def test_a_hand_written_class_after_the_builder_block_is_unsupported() -> None:
    graph = single_indicator_graph("sma")
    source = generate(graph) + "\n\nclass Extra(Strategy):\n    name = 'extra'\n"
    result = recognize(source)
    assert result.graph == graph
    assert any("Extra" in span.reason for span in result.unsupported)
