"""The StrategyGraph model: catalogue derivation, canonical hashing, and error-code parity.

The parity test (graph.validate vs ``verify_manifest``) needs the native extension and is
skipped cleanly when it is unavailable; the rest is pure Python.
"""

from __future__ import annotations

import pytest

from honba.entities.instrument import InstrumentId
from honba.strategies.graph import (
    GRAPH_SCHEMA_VERSION,
    GraphError,
    StrategyGraph,
    indicator_catalogue,
    node_catalogue,
    node_spec,
)

X = InstrumentId("TCS", "NSE")


def golden(**changes: object) -> StrategyGraph:
    graph = StrategyGraph.build(
        "sma_cross",
        universe=[X],
        driving_timeframe=(1, "minute"),
        warmup_bars=20,
        nodes=[
            {"id": "n1", "type": "universe", "label": "Universe"},
            {
                "id": "n2",
                "type": "indicator",
                "indicator": "sma",
                "label": "Fast SMA",
                "params": {"period": 20},
            },
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
    if not changes:
        return graph
    # ``model_copy(update=...)`` skips nested validation, so re-validate through plain JSON.
    document = graph.model_dump(mode="json")
    document.update(changes)
    return StrategyGraph.model_validate(document)


def test_a_valid_graph_passes_validation() -> None:
    golden().validate()


def test_canonical_hash_is_stable_and_order_independent() -> None:
    assert golden().graph_hash() == golden().graph_hash()
    assert golden().graph_hash().startswith("sha256:")
    # Canonical JSON sorts keys, so a re-serialised graph hashes the same.
    assert (
        StrategyGraph.model_validate(golden().canonical_dict()).graph_hash()
        == golden().graph_hash()
    )


def test_the_node_catalogue_comes_from_the_registered_indicators() -> None:
    catalogue = indicator_catalogue()
    assert "sma" in catalogue and "rsi" in catalogue
    sma = catalogue["sma"]
    assert sma["family"] == "moving_average"
    assert sma["outputs"] == ["value"]
    period = next(p for p in sma["params"] if p["name"] == "period")
    assert period["min"] == 1 and period["step"] == 1 and period["max"] >= period["default"]
    assert "universe" in node_catalogue()["node_types"]
    assert node_spec("condition")["operators"]


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"name": "  "}, "empty_name"),
        ({"driving_timeframe": {"interval": 0, "aggregation": "minute"}}, "zero_interval"),
        (
            {
                "universe": {"explicit": []},
                "nodes": [{"id": "n1", "type": "universe"}],
                "edges": [],
            },
            "no_subscriptions",
        ),
        (
            {
                "universe": {"named": "NIFTY50"},
                "nodes": [{"id": "n1", "type": "universe"}],
                "edges": [],
            },
            "named_universe_unresolved",
        ),
    ],
)
def test_manifest_level_codes_match_honba_verify(changes: dict, code: str) -> None:
    graph = golden(**changes)
    with pytest.raises(GraphError) as caught:
        graph.validate()
    assert caught.value.code == code
    # Parity: the same defect through the Rust compiler carries the same code.
    _native = pytest.importorskip("honba._honba")
    from honba.strategies.verify import VerifyError, verify_manifest

    with pytest.raises(VerifyError) as rust:
        verify_manifest(graph.to_manifest())
    assert rust.value.code == code


def test_graph_only_codes() -> None:
    cases = [
        (golden(nodes=[]), "empty_graph"),
        (
            golden(
                nodes=[
                    {"id": "n1", "type": "universe"},
                    {"id": "n1", "type": "indicator", "indicator": "sma"},
                ]
            ),
            "duplicate_node_id",
        ),
        (golden(nodes=[{"id": "n1", "type": "bogus"}]), "unknown_node_type"),
        (
            golden(nodes=[{"id": "n1", "type": "indicator", "indicator": "nope"}]),
            "unknown_indicator",
        ),
        (
            golden(
                nodes=[{"id": "n1", "type": "indicator", "indicator": "sma", "params": {"nope": 1}}]
            ),
            "unknown_node_param",
        ),
        (
            golden(
                edges=[
                    {
                        "id": "e1",
                        "from_node": "n1",
                        "from_port": "bars",
                        "to_node": "zz",
                        "to_port": "input",
                    }
                ]
            ),
            "unknown_edge_endpoint",
        ),
        (
            golden(
                edges=[
                    {
                        "id": "e1",
                        "from_node": "n1",
                        "from_port": "nope",
                        "to_node": "n2",
                        "to_port": "input",
                    }
                ]
            ),
            "unknown_port",
        ),
    ]
    for graph, code in cases:
        with pytest.raises(GraphError) as caught:
            graph.validate()
        assert caught.value.code == code, code


def test_schema_version_is_checked() -> None:
    with pytest.raises(GraphError) as caught:
        golden(schema_version=GRAPH_SCHEMA_VERSION + 1).validate()
    assert caught.value.code == "unsupported_schema_version"


def test_to_manifest_bridges_to_the_rust_manifest() -> None:
    manifest = golden().to_manifest()
    assert manifest.name == "sma_cross"
    assert manifest.subscriptions.instruments
    assert manifest.driving_timeframe.aggregation.value == "minute"
    # Python-only provenance is stripped from the Rust shape.
    payload = manifest.to_json_dict()
    assert "graph_hash" not in payload and "sidecar" not in payload
