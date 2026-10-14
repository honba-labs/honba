"""AI module for Honba: MCP gateway, LLM integration, journal data, trust envelope, typed decisions."""

from honba.ai.alignment import GRPOReward, QLoRATrainer, SyntheticDataset
from honba.ai.decision import (
    DecisionKind,
    LlmDecision,
    apply_llm_decision,
    validate_llm_decision,
)
from honba.ai.gbnf import build_grammar, ebnf_to_gbnf
from honba.ai.local_llm import LocalLlm
from honba.ai.report import (
    FactSnapshot,
    QualityReport,
    QualityViolation,
    Report,
    build_report,
    build_snapshot,
    quality_gate,
)
from honba.ai.trust import (
    TrustEnvelope,
    extract_from_llm_rendered,
    render_for_llm,
    wrap_untrusted,
)

__all__ = [
    "DecisionKind",
    "FactSnapshot",
    "GRPOReward",
    "LlmDecision",
    "LocalLlm",
    "QLoRATrainer",
    "QualityReport",
    "QualityViolation",
    "Report",
    "SyntheticDataset",
    "TrustEnvelope",
    "apply_llm_decision",
    "build_grammar",
    "build_report",
    "build_snapshot",
    "ebnf_to_gbnf",
    "extract_from_llm_rendered",
    "quality_gate",
    "render_for_llm",
    "validate_llm_decision",
    "wrap_untrusted",
]
