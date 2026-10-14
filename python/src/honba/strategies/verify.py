"""Verify a strategy manifest: compile it to the Rust ``StrategyIr`` (docs/archive/plan.md E0-S8).

The IR is the manifest plus everything a runner would otherwise infer: the concrete
universe, per-channel subscription lists, the timeframes to load and the warm-up.
Resolution rules live once, in ``honba_strategy::StrategyIr::compile``; this module is
glue over ``honba._honba.verify_manifest``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from honba._native import native_attr
from honba.strategies.manifest import StrategyManifest

__all__ = ["VerifyError", "source_bundle_hash", "verify_manifest", "verify_strategy_source"]

SIDECAR_SUFFIX = ".strategy.json"
"""The optional sidecar written beside a generated ``.py`` (ADR 0029, D16)."""


def source_bundle_hash(source: str | bytes, sidecar: str | bytes | None = None) -> str:
    """``sha256:`` plus the digest of the generated source and its optional sidecar.

    The two parts are joined with a NUL byte so a file and a sidecar cannot collide by
    concatenation; without a sidecar the hash is over the source alone.
    """
    first = source.encode("utf-8") if isinstance(source, str) else source
    if sidecar is None:
        digest = hashlib.sha256(first).hexdigest()
    else:
        second = sidecar.encode("utf-8") if isinstance(sidecar, str) else sidecar
        digest = hashlib.sha256(first + b"\x00" + second).hexdigest()
    return f"sha256:{digest}"


def verify_strategy_source(
    path: str | Path,
    *,
    sidecar: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a generated strategy file plus its optional ``.strategy.json`` sidecar.

    The graph comes from the sidecar (a graph document) or, when there is none, from the
    builder block recovered from the source (``graph_recognize``). ``source_hash`` covers both
    files; ``graph_hash`` is the canonical graph digest. The compiled IR is returned, exactly
    as :func:`verify_manifest`. Raises :class:`VerifyError` with code ``no_graph_source`` when
    neither a sidecar nor a builder block is present, or a graph/manifest code otherwise.
    """
    from honba.strategies.graph import GraphError, StrategyGraph
    from honba.strategies.graph_recognize import recognize

    source_path = Path(path)
    source_text = source_path.read_text(encoding="utf-8")
    sidecar_path = Path(sidecar) if sidecar is not None else source_path.with_suffix(SIDECAR_SUFFIX)
    sidecar_text: str | None = None
    if sidecar_path.is_file():
        sidecar_text = sidecar_path.read_text(encoding="utf-8")
    elif sidecar is not None:
        raise VerifyError("no_sidecar", f"sidecar {sidecar_path} does not exist")

    graph: StrategyGraph | None = None
    if sidecar_text is not None:
        try:
            document = json.loads(sidecar_text)
        except ValueError as exc:
            raise VerifyError("deserialize", f"sidecar is not valid JSON: {exc}") from None
        try:
            graph = StrategyGraph.model_validate(document)
        except ValueError as exc:
            raise VerifyError("deserialize", f"sidecar is not a strategy graph: {exc}") from None
    else:
        found = recognize(source_text)
        graph = found.graph
    if graph is None:
        raise VerifyError(
            "no_graph_source",
            "no .strategy.json sidecar and no # honba:strategy-graph builder block in the source",
        )

    try:
        manifest = graph.to_manifest()
    except GraphError as exc:
        raise VerifyError(exc.code, exc.message) from None
    manifest = manifest.model_copy(
        update={
            "source_hash": source_bundle_hash(source_text, sidecar_text),
            "graph_hash": graph.graph_hash(),
            "sidecar": str(sidecar_path) if sidecar_text is not None else None,
        }
    )
    try:
        manifest.validate_manifest()
    except ValueError as exc:
        code = getattr(exc, "code", "invalid_manifest")
        raise VerifyError(code, str(exc)) from None
    return verify_manifest(manifest)


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
