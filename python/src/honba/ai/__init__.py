"""AI module for Honba: MCP gateway, LLM integration, journal data, trust envelope."""

from honba.ai.trust import (
    TrustEnvelope,
    extract_from_llm_rendered,
    render_for_llm,
    wrap_untrusted,
)

__all__ = [
    "TrustEnvelope",
    "extract_from_llm_rendered",
    "render_for_llm",
    "wrap_untrusted",
]
