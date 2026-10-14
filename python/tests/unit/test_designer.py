"""Designer control plane: manifest provenance, verify errors, checklist, versions, helpers."""

from __future__ import annotations

import pytest

from honba.entities.instrument import InstrumentId
from honba.strategies.designer import (
    checklist_for_publish,
    diff_graphs,
    drawdown_series,
    graph_source_lines,
    graph_version,
    persist_version,
    restore_graph,
    run_mode_badge,
    trust_badges,
    verify_graph,
)
from honba.strategies.graph import StrategyGraph
from honba.strategies.graph_codegen import generate

X = InstrumentId("TCS", "NSE")


def golden() -> StrategyGraph:
    return StrategyGraph.build(
        "sma_cross",
        universe=[X],
        driving_timeframe=(1, "minute"),
        warmup_bars=20,
        nodes=[
            {"id": "n1", "type": "universe", "label": "Universe"},
            {"id": "n2", "type": "indicator", "indicator": "sma", "params": {"period": 20}},
            {"id": "o", "type": "order", "label": "Entry", "params": {"side": "buy"}},
        ],
        edges=[
            {
                "id": "e1",
                "from_node": "n1",
                "from_port": "bars",
                "to_node": "n2",
                "to_port": "input",
            },
        ],
    )


def test_manifest_carries_graph_hash_but_rust_never_sees_it() -> None:
    graph = golden()
    manifest = graph.to_manifest()
    payload = manifest.to_json_dict()
    assert manifest.source_hash == graph.graph_hash()
    assert "graph_hash" not in payload and "sidecar" not in payload
    carried = manifest.model_copy(
        update={"graph_hash": graph.graph_hash(), "sidecar": "s.strategy.json"}
    )
    assert carried.graph_hash == graph.graph_hash()
    assert "graph_hash" not in carried.to_json_dict()


def _restage(graph: StrategyGraph, **changes: object) -> StrategyGraph:
    """Apply ``changes`` through plain-JSON re-validation (``model_copy`` skips nesting)."""
    document = graph.model_dump(mode="json")
    document.update(changes)
    return StrategyGraph.model_validate(document)


def test_verify_errors_name_the_node_and_source_line() -> None:
    graph = _restage(
        golden(),
        nodes=[
            {"id": "n1", "type": "universe"},
            {"id": "bad", "type": "indicator", "indicator": "missing"},
        ],
    )
    result = verify_graph(graph, compile=False)
    assert not result.ok
    (issue,) = result.issues
    assert issue.code == "unknown_indicator"
    assert issue.node_id == "bad"
    assert issue.lineno == graph_source_lines(generate(graph))["bad"]


def test_verify_compiles_to_the_ir_when_native_is_available() -> None:
    pytest.importorskip("honba._honba")
    result = verify_graph(golden())
    assert result.ok and result.ir is not None
    assert result.ir["manifest"]["name"] == "sma_cross"


def test_publish_checklist_covers_graph_entry_and_validation() -> None:
    graph = golden()
    items = checklist_for_publish(graph, verify=verify_graph(graph, compile=False))
    keys = {i.key: i.ok for i in items}
    assert keys == {"graph_valid": True, "has_order": True, "compiles": True}
    items = checklist_for_publish(
        _restage(graph, nodes=[{"id": "n1", "type": "universe"}]),
        validation={"passed": True, "dsr": {"passed": True}},
    )
    keys = {i.key: i.ok for i in items}
    assert keys["graph_valid"] is False and keys["has_order"] is False
    assert keys["validation_passed"] is True


def test_version_diff_and_restore_round_trip() -> None:
    base, other = golden(), golden()
    assert diff_graphs(base, other).is_empty
    assert graph_version(base).graph_hash == base.graph_hash()
    assert restore_graph(graph_version(base)) == base
    changed_doc = other.model_dump(mode="json")
    changed_doc["nodes"][-1]["params"] = {"side": "sell"}
    changed = StrategyGraph.model_validate(changed_doc)
    diff = diff_graphs(base, changed)
    assert diff.changed_nodes == ("o",) and diff.is_empty is False


def test_versions_persist_under_the_content_hash(tmp_path) -> None:
    path = persist_version(golden(), tmp_path)
    assert path.name == f"{golden().graph_hash().split(':')[1]}.strategy.json"
    assert restore_graph(graph_version(golden())) == golden()


def test_run_mode_badge_blocks_unapproved_live() -> None:
    assert run_mode_badge("paper")["blocked"] is False
    assert run_mode_badge("live", kill_switch=True)["blocked"] is True
    assert run_mode_badge("live", kill_switch=False, approved=False)["blocked"] is True
    assert run_mode_badge("live", kill_switch=False, approved=True)["blocked"] is False
    with pytest.raises(ValueError):
        run_mode_badge("nope")


def test_trust_badges_and_drawdown_are_pure() -> None:
    badges = trust_badges(
        {"passed": True, "dsr": {"passed": True}, "pbo": {"passed": False}, "walk_forward": None}
    )
    assert {(b["key"], b["passed"]) for b in badges} == {("dsr", True), ("pbo", False)}
    assert trust_badges(None) == []
    assert drawdown_series([100.0, 120.0, 90.0, 90.0, 150.0]) == [0.0, 0.0, 25.0, 25.0, 0.0]
