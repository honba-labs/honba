"""LLM provider port and implementations (Design.md Section 13.4).

Provides an abstract LlmPort and a ScriptedFakeLlm for deterministic testing
and offline scaffolding when no local or remote LLM is running.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class LlmPort(ABC):
    """Abstract interface for LLM completions."""

    @abstractmethod
    def complete(
        self,
        messages: Sequence[dict[str, str]],
        grammar: str | None = None,
        schema: dict[str, Any] | None = None,
        seed: int | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Generate a completion for the given messages.

        Args:
            messages: List of chat messages (e.g. [{"role": "user", "content": "..."}]).
            grammar: Optional GBNF/EBNF grammar string for constrained sampling.
            schema: Optional JSON schema for structured JSON output.
            seed: Optional random seed for reproducible completions.
            temperature: Sampling temperature (default 0.0 for deterministic).

        Returns:
            The raw string response from the model.
        """
        ...


class ScriptedFakeLlm(LlmPort):
    """Deterministic scripted LLM for testing and scaffolding.

    Can return pre-scripted responses sequentially, or simulate repair loops.
    """

    def __init__(self, responses: Sequence[str | Exception] | None = None, default_response: str = "") -> None:
        self.responses = list(responses) if responses else []
        self.default_response = default_response
        self.call_history: list[dict[str, Any]] = []

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        grammar: str | None = None,
        schema: dict[str, Any] | None = None,
        seed: int | None = None,
        temperature: float = 0.0,
    ) -> str:
        self.call_history.append(
            {
                "messages": list(messages),
                "grammar": grammar,
                "schema": schema,
                "seed": seed,
                "temperature": temperature,
            }
        )
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return self.default_response
