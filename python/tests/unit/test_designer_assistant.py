"""The assistant hook: proposals become discussion / candidate / validation_error (E9-S5)."""

from __future__ import annotations

from honba.ai.designer_assistant import AssistantProposal, route_proposal
from honba.entities.instrument import InstrumentId
from honba.strategies.graph import StrategyGraph

X = InstrumentId("TCS", "NSE")


def golden() -> StrategyGraph:
    return StrategyGraph.build(
        "sma_cross",
        universe=[X],
        driving_timeframe=(1, "minute"),
        warmup_bars=20,
        nodes=[
            {"id": "n1", "type": "universe"},
            {"id": "n2", "type": "indicator", "indicator": "sma", "params": {"period": 20}},
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


class Verdict:
    """A minimal verification report shaped like the E5-S5 verdict."""

    def __init__(self, ok: bool, findings=()) -> None:
        self.ok = ok
        self.findings = findings


def clean(source: str, **kwargs: object) -> Verdict:
    return Verdict(True)


def dirty(source: str, **kwargs: object) -> Verdict:
    class Finding:
        def to_dict(self) -> dict:
            return {"code": "smoke_failed", "message": "nope", "lineno": 1, "col": None}

    return Verdict(False, (Finding(),))


def test_a_message_with_no_candidate_is_a_discussion() -> None:
    result = route_proposal(
        AssistantProposal(message="what if we shorten the fast leg?"), verifier=clean
    )
    assert result.kind == "discussion"
    assert result.graph is None


def test_a_clean_candidate_carries_its_diff() -> None:
    graph = golden()
    document = graph.model_dump(mode="json")
    document["nodes"] = [n for n in document["nodes"] if n["id"] != "n2"]
    smaller = StrategyGraph.model_validate(document)
    result = route_proposal(
        AssistantProposal(message="drop the indicator", graph=smaller),
        current=graph,
        verifier=clean,
    )
    assert result.kind == "candidate"
    assert result.source is not None
    assert result.diff is not None and result.diff.removed_nodes == ("n2",)


def test_a_rejected_candidate_is_a_validation_error_with_findings() -> None:
    result = route_proposal(AssistantProposal(message="try this", graph=golden()), verifier=dirty)
    assert result.kind == "validation_error"
    assert result.findings and result.verify is not None


def test_typed_decisions_ride_along_with_the_result() -> None:
    decisions = (
        {
            "order_id": "1",
            "symbol": "TCS",
            "side": "buy",
            "original_quantity": 10.0,
            "decision": "veto",
            "decided_quantity": 0.0,
            "rationale": "news",
        },
    )
    result = route_proposal(AssistantProposal(message="veto", decisions=decisions), verifier=clean)
    assert result.kind == "discussion"
    (decision,) = result.decisions
    assert decision.order_id == "1" and decision.decision.value == "veto"
