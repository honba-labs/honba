"""Verify a strategy manifest: compile it to the Rust ``StrategyIr`` (plan.md E0-S8).

The IR is the manifest plus everything a runner would otherwise infer: the concrete
universe, per-channel subscription lists, the timeframes to load and the warm-up.
Resolution rules live once, in ``honba_strategy::StrategyIr::compile``; this module is
glue over ``honba._honba.verify_manifest``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from honba._native import native_attr
from honba.strategies.manifest import StrategyManifest

__all__ = ["VerifyError", "verify_manifest"]


class VerifyError(ValueError):
    """A manifest that does not compile. ``code`` is ``deserialize`` or the Rust
    ``IrError::code()`` (manifest codes match :class:`ManifestError`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def verify_manifest(manifest: StrategyManifest | Mapping[str, Any] | str) -> dict[str, Any]:
    """Compile ``manifest`` (model, JSON-shaped mapping or JSON text) to its IR dict."""
    if isinstance(manifest, StrategyManifest):
        text = json.dumps(manifest.to_json_dict())
    elif isinstance(manifest, str):
        text = manifest
    else:
        text = json.dumps(dict(manifest))
    try:
        return json.loads(native_attr("verify_manifest")(text))
    except ValueError as exc:
        code, _, message = str(exc).partition(": ")
        raise VerifyError(code, message) from None
