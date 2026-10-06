"""Lazy access to the compiled extension ``honba._honba``.

``import honba`` must work in a source-only checkout, so nothing imports the extension at
module load. Native-backed values and calls go through :func:`native` / :func:`native_attr`,
which fail on first use with a clear error:

* extension missing: ``ImportError`` (build it with ``maturin develop``);
* extension stale: ``RuntimeError`` naming the missing symbol (rebuild it).
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = ["native", "native_attr"]


def native() -> ModuleType:
    """Return ``honba._honba``, or raise ``ImportError`` if it is not built."""
    try:
        return importlib.import_module("honba._honba")
    except ImportError as exc:
        raise ImportError(
            "the honba native extension (honba._honba) is not available; build it with "
            "`maturin develop` (or cargo build -p honba-py and copy the shared library "
            "into python/src/honba/)"
        ) from exc


def native_attr(name: str) -> Any:
    """Return ``honba._honba.<name>``; ``RuntimeError`` if the extension is stale."""
    ext = native()
    try:
        return getattr(ext, name)
    except AttributeError:
        raise RuntimeError(
            f"honba._honba has no {name!r}: the compiled extension is stale, "
            "rebuild it (maturin develop)"
        ) from None
