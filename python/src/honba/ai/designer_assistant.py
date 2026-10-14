"""AI assistant hook for the AlgoDesigner (E9-S5, ADR 0029).

The assistant may only *propose*: a discussion message, or a graph/code **diff** to the current
strategy. It never runs code and never mutates the design. Every candidate is routed through the
existing safety surfaces before it can become a result:

* the AI strategy verifier (E5-S5, :func:`honba.ai.verification.verify_source`) — static scan plus
  sandboxed smoke/look-ahead checks; a candidate that fails is returned as a ``validation_error``
  carrying the findings, never as a runnable candidate;
* the typed decision model (E5-S7, :func:`honba.ai.decision.validate_llm_decision`) for any
  order-level decisions the assistant attaches (veto / size-down only).

The result is one typed value: ``discussion``, ``candidate`` or ``validation_error``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from honba.ai.decision import LlmDecision, validate_llm_decision
from honba.ai.verification.verifier import VerificationReport
from honba.strategies.designer import GraphDiff, diff_graphs
from honba.strategies.graph import StrategyGraph
from honba.strategies.graph_codegen import generate

__all__ = [
    "ASSISTANT_KINDS",
    "Assistant",
    "AssistantProposal",
    "AssistantResult",
    "route_proposal",
]

ASSISTANT_KINDS: tuple[str, ...] = ("discussion", "candidate", "validation_error")
"""The typed outcomes of routing an assistant proposal."""

Verifier = Callable[..., VerificationReport]


class Assistant(Protocol):
    """What the designer needs from an assistant: propose, never execute."""

    def propose(self, prompt: str, current: StrategyGraph | None) -> AssistantProposal: ...


@dataclass(frozen=True, slots=True)
class AssistantProposal:
    """An assistant's raw proposal: a discussion message and/or a candidate graph or code."""

    message: str = ""
    graph: StrategyGraph | None = None
    source: str | None = None
    decisions: tuple[Mapping[str, Any], ...] = ()

    @property
    def is_candidate(self) -> bool:
        return self.graph is not None or self.source is not None


@dataclass(frozen=True, slots=True)
class AssistantResult:
    """A routed proposal: one typed ``kind`` plus the diff/findings/decisions that justify it."""

    kind: str
    message: str = ""
    graph: StrategyGraph | None = None
    source: str | None = None
    diff: GraphDiff | None = None
    findings: tuple[Any, ...] = ()
    decisions: tuple[LlmDecision, ...] = ()
    verify: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "source": self.source,
            "findings": [f.to_dict() for f in self.findings if hasattr(f, "to_dict")],
            "decisions": [d.to_journal_record() for d in self.decisions],
        }


def _default_verifier() -> Verifier:
    from honba.ai.verification.verifier import verify_source

    return verify_source


def route_proposal(
    proposal: AssistantProposal,
    *,
    current: StrategyGraph | None = None,
    verifier: Verifier | None = None,
    bars: Sequence[Any] | None = None,
) -> AssistantResult:
    """Route ``proposal`` through the verifier and typed decisions; return a typed result.

    A proposal with no candidate is a ``discussion``. A candidate (graph or source) is compiled
    to source, diffed against ``current``, and verified; a clean candidate is a ``candidate``,
    a rejected one a ``validation_error`` carrying the verifier's findings.
    """
    if not proposal.is_candidate:
        return AssistantResult(
            kind="discussion",
            message=proposal.message,
            graph=None,
            decisions=_decisions(proposal.decisions),
        )

    graph = proposal.graph
    source = proposal.source
    if source is None and graph is not None:
        source = generate(graph)
    assert source is not None

    verify_fn = verifier or _default_verifier()
    report = verify_fn(source, bars=bars) if bars is not None else verify_fn(source)
    diff = diff_graphs(current, graph) if graph is not None and current is not None else None
    decisions = _decisions(proposal.decisions)

    if not report.ok:
        return AssistantResult(
            kind="validation_error",
            message=proposal.message,
            graph=graph,
            source=source,
            diff=diff,
            findings=tuple(report.findings),
            decisions=decisions,
            verify=report,
        )
    return AssistantResult(
        kind="candidate",
        message=proposal.message,
        graph=graph,
        source=source,
        diff=diff,
        decisions=decisions,
        verify=report,
    )


def _decisions(raw: Sequence[Mapping[str, Any]]) -> tuple[LlmDecision, ...]:
    out: list[LlmDecision] = []
    for item in raw:
        out.append(validate_llm_decision(dict(item)))
    return tuple(out)
