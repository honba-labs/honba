"""Trust envelope for untrusted text.

Wraps untrusted content (broker messages, LLM output, instrument names, free text from
adapters) in a typed envelope that carries:
- `origin`: source identifier
- `content_hash`: SHA-256 hash of the content
- `untrusted`: explicit marker (always True)
- Helper to render for LLM prompts with unambiguous delimiters
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

__all__ = ["TrustEnvelope", "extract_from_llm_rendered", "render_for_llm", "wrap_untrusted"]

_HASH_LEN = 16
_PREFIX = "[TRUSTED_ENVELOPE:"


@dataclass(frozen=True, slots=True)
class TrustEnvelope:
    """Envelope marking content as untrusted with provenance and integrity hash."""

    content: str
    origin: str
    untrusted: bool = True
    content_hash: str = field(init=False, repr=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_hash", self._compute_hash())

    def _compute_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:_HASH_LEN]

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "content": self.content,
                "origin": self.origin,
                "untrusted": self.untrusted,
                "content_hash": self.content_hash,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> TrustEnvelope:
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        envelope = cls(content=data["content"], origin=data["origin"])
        # Verify hash matches
        if envelope.content_hash != data.get("content_hash"):
            raise ValueError("Content hash mismatch: envelope may have been tampered with")
        # Verify untrusted flag from JSON
        if not data.get("untrusted", True):
            raise ValueError("Envelope must have untrusted=True")
        return envelope


def wrap_untrusted(content: str, origin: str) -> TrustEnvelope:
    """Wrap untrusted text in a trust envelope.

    Args:
        content: The untrusted text content
        origin: Source identifier (e.g., "broker:dhan", "llm:claude", "instrument:name")

    Returns:
        TrustEnvelope with content_hash, origin, and untrusted=True
    """
    return TrustEnvelope(content=content, origin=origin)


def render_for_llm(envelope: TrustEnvelope) -> str:
    """Render trust envelope for LLM prompt with unambiguous delimiters.

    Uses length-prefix encoding for both the origin and the content, so neither can
    close the envelope early: the length header, not a delimiter, decides where the
    content ends. Format::

        [TRUSTED_ENVELOPE:<origin_len>:<origin>:<hash>:<content_len>:<content>]

    Args:
        envelope: TrustEnvelope to render

    Returns:
        Rendered string safe for LLM consumption
    """
    content = envelope.content
    origin = envelope.origin
    return f"{_PREFIX}{len(origin)}:{origin}:{envelope.content_hash}:{len(content)}:{content}]"


def extract_from_llm_rendered(rendered: str) -> TrustEnvelope | None:
    """Extract trust envelope from an LLM-rendered string.

    Returns ``None`` if the string is not a valid trust envelope rendering, or if the
    recovered content does not match its recorded hash.
    """
    if not rendered.startswith(_PREFIX) or not rendered.endswith("]"):
        return None

    inner = rendered[len(_PREFIX) : -1]

    # origin is length-prefixed, so it may itself contain colons.
    colon = inner.find(":")
    if colon < 0:
        return None
    try:
        origin_len = int(inner[:colon])
    except ValueError:
        return None
    rest = inner[colon + 1 :]
    if len(rest) < origin_len:
        return None
    origin = rest[:origin_len]
    rest = rest[origin_len:]
    if not rest.startswith(":"):
        return None
    rest = rest[1:]

    content_hash = rest[:_HASH_LEN]
    rest = rest[_HASH_LEN:]
    if not rest.startswith(":"):
        return None
    rest = rest[1:]

    colon = rest.find(":")
    if colon < 0:
        return None
    try:
        content_len = int(rest[:colon])
    except ValueError:
        return None
    content = rest[colon + 1 :]
    if len(content) != content_len:
        return None

    computed_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:_HASH_LEN]
    if computed_hash != content_hash:
        return None

    return TrustEnvelope(content=content, origin=origin)
