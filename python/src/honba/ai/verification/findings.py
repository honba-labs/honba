"""Typed findings for the AI strategy verifier (E5-S5).

Every failure mode that can cross the verifier's surface carries one stable string code, so a
caller (research loop, MCP tool, future CLI) can branch on the code rather than parse a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Finding", "FindingCode"]


class FindingCode:
    """Stable codes carried by :class:`Finding` (shared by static scan and sandbox run)."""

    FORBIDDEN_IMPORT = "forbidden_import"
    FORBIDDEN_NAME = "forbidden_name"
    FORBIDDEN_DUNDER = "forbidden_dunder"
    SYNTAX_ERROR = "syntax_error"
    NO_STRATEGY = "no_strategy"
    MULTIPLE_STRATEGIES = "multiple_strategies"
    CONSTRUCTION_ERROR = "construction_error"
    SMOKE_FAILED = "smoke_failed"
    NONDETERMINISTIC = "nondeterministic"
    LOOK_AHEAD = "look_ahead"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    SANDBOX_LIMIT = "sandbox_limit"
    SANDBOX_DENIED = "sandbox_denied"
    SANDBOX_CRASH = "sandbox_crash"


@dataclass(frozen=True, slots=True)
class Finding:
    """A single verifier finding: a stable ``code``, a human message and an optional location."""

    code: str
    message: str
    lineno: int | None = None
    col: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """The finding as a JSON-shaped mapping (used across the sandbox boundary)."""
        return {"code": self.code, "message": self.message, "lineno": self.lineno, "col": self.col}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        """Rebuild a finding from the JSON-shaped mapping produced by :meth:`to_dict`."""
        return cls(
            code=str(data["code"]),
            message=str(data["message"]),
            lineno=data.get("lineno"),
            col=data.get("col"),
        )
