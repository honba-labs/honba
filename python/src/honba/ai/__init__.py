"""AI module for Honba: MCP gateway, LLM integration, journal data, trust envelope, typed decisions."""

from honba.ai.decision import (
    DecisionKind,
    LlmDecision,
    apply_llm_decision,
    validate_llm_decision,
)
from honba.ai.trust import (
    TrustEnvelope,
    extract_from_llm_rendered,
    render_for_llm,
    wrap_untrusted,
)

__all__ = [
    "DecisionKind",
    "LlmDecision",
    "TrustEnvelope",
    "apply_llm_decision",
    "extract_from_llm_rendered",
    "render_for_llm",
    "validate_llm_decision",
    "wrap_untrusted",
]
