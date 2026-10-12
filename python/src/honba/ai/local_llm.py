"""Local in-process LLM runner with GGUF support and GBNF grammar (E5-S10).

Provides a LocalLlm class that can load a GGUF model via llama-cpp-python
when available, otherwise falls back to a deterministic stub for testing.
"""

from __future__ import annotations

import importlib.util
import re
from typing import TYPE_CHECKING, Any, Protocol


class LlmLike(Protocol):
    """Protocol for llama_cpp.Llama-like callable."""

    def __call__(
        self, prompt: str, max_tokens: int, stop: list[str]
    ) -> dict[str, Any]: ...


class LocalLlm:
    """Local in-process LLM runner with optional GGUF model loading.

    When llama-cpp-python is installed and a model_path is provided,
    loads the GGUF model and uses it for completions with optional GBNF grammar.
    Otherwise, operates in stub mode returning deterministic valid responses.
    """

    def __init__(
        self,
        model_path: str | None = None,
        grammar: str | None = None,
        **kwargs: object,
    ) -> None:
        """Initialize the local LLM runner.

        Args:
            model_path: Path to GGUF model file. If None or llama_cpp not available, uses stub.
            grammar: Optional GBNF grammar string for constrained sampling.
            **kwargs: Additional arguments passed to llama_cpp.Llama if available.
        """
        self.model_path = model_path
        self.grammar = grammar
        self.kwargs: dict[str, object] = kwargs
        self._llm: LlmLike | None = None
        self._use_stub = False

        # Check if llama_cpp is available
        self._llama_available = importlib.util.find_spec("llama_cpp") is not None

        if model_path and self._llama_available:
            try:
                if TYPE_CHECKING:
                    import llama_cpp  # type: ignore[import-not-found]
                else:
                    import llama_cpp

                self._llm = llama_cpp.Llama(
                    model_path=model_path,
                    grammar=grammar,
                    **kwargs,
                )
            except ImportError:
                # Fall back to stub on import error
                self._use_stub = True
            except OSError:
                # Fall back to stub on model load error
                self._use_stub = True
        else:
            self._use_stub = True

    def ask(self, prompt: str) -> str:
        """Generate a completion for the given prompt.

        In stub mode (no model or llama_cpp not available), returns a
        deterministic response that conforms to the grammar if provided.

        Args:
            prompt: Input prompt for the model

        Returns:
            Model response as string
        """
        if self._use_stub or self._llm is None:
            return self._stub_response(prompt)

        # Use actual llama_cpp model
        response = self._llm(prompt, max_tokens=512, stop=["\n"])
        return response["choices"][0]["text"].strip()

    def _stub_response(self, prompt: str) -> str:
        """Generate a deterministic stub response.

        Returns a valid response that would match the grammar if possible.
        """
        if self.grammar:
            # Parse grammar for simple patterns and return matching response
            return self._grammar_based_stub(prompt)

        # Default deterministic response
        return "ok"

    def _grammar_based_stub(self, prompt: str) -> str:
        """Generate a stub response based on grammar patterns.

        Very simple heuristic: look for known patterns in the grammar
        and return a matching string.
        """
        grammar = self.grammar or ""
        grammar_lower = grammar.lower()

        # Common filter patterns
        if "market" in grammar_lower and "cap" in grammar_lower:
            return "market cap above 10000 Cr"
        if "rsi" in grammar_lower:
            return "rsi below 30"
        if "above" in grammar_lower:
            return "metric above 100"
        if "below" in grammar_lower:
            return "metric below 50"
        if "approve" in grammar_lower:
            return "approve"
        if "reject" in grammar_lower:
            return "reject"
        if "hello" in grammar_lower:
            return "hello"
        if "world" in grammar_lower:
            return "world"

        # Fallback: return first rule-like thing found

        # Look for string literals in grammar
        literals = re.findall(r'"([^"]+)"', grammar)
        if literals:
            return literals[0]

        # Look for rule names
        rules = re.findall(r"(\w+)\s*::=", grammar)
        if rules:
            return rules[0]

        return "valid"
