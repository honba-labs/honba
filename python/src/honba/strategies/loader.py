"""Load strategies from a ``.py`` file or from a ``honba-strategies`` catalog by registry name.

Infrastructure at the edge: it imports user code. The catalog is a checkout, not an
installed package. Strategies are found through its ``registry.json`` (the top-level
index, then per-strategy ``registry.json`` files) rather than hardcoded paths.

Catalog location (:func:`find_catalog`), first match wins and nothing depends on the
working directory: an explicit path, ``$HONBA_STRATEGIES_DIR``, then (only when the
caller passes ``search_from``) a ``honba-strategies`` directory beside ``search_from``
or one of its parents.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from honba.strategies.base import Strategy
from honba.strategies.config import StrategyConfig

__all__ = [
    "CATALOG_DIRNAME",
    "CATALOG_ENV",
    "CatalogError",
    "CatalogStrategy",
    "find_catalog",
    "load_catalog_strategy",
    "load_strategy",
]

CATALOG_ENV = "HONBA_STRATEGIES_DIR"
CATALOG_DIRNAME = "honba-strategies"


class CatalogError(RuntimeError):
    """A strategy file, the catalog, or a strategy in it could not be found or loaded."""


@dataclass(frozen=True)
class CatalogStrategy:
    """A catalog strategy with its config and a digest for run provenance."""

    name: str
    path: Path  # strategy directory
    cls: type[Strategy]
    config: StrategyConfig
    module: ModuleType
    source_sha256: str  # sha256 of strategy.py + config.toml

    def instantiate(self) -> Strategy:
        """Build the strategy: ``cls(config)`` if its constructor takes one, else ``cls()``."""
        return _instantiate(self.cls, self.config)


def _instantiate(cls: type[Strategy], config: StrategyConfig | None) -> Strategy:
    params = [
        p
        for p in inspect.signature(cls.__init__).parameters.values()
        if p.name != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if params and config is not None:
        return cls(config)  # type: ignore[call-arg]
    return cls()


def _import_file(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CatalogError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so decorators (``@dataclass`` with postponed annotations)
    # can look the module up; removed again if the import fails.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _strategy_classes(module: ModuleType) -> list[type[Strategy]]:
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == module.__name__
    ]


def load_strategy(path: str | Path) -> type[Strategy]:
    """Import ``path`` and return the one ``Strategy`` subclass it defines."""
    path = Path(path).resolve()
    if not path.is_file():
        raise CatalogError(f"strategy file {path} not found")
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
    module = _import_file(path, f"honba_user_strategy_{path.stem}_{digest}")
    classes = _strategy_classes(module)
    if len(classes) != 1:
        found = "no Strategy subclass" if not classes else f"{len(classes)} Strategy subclasses"
        raise CatalogError(f"{path}: expected exactly one Strategy subclass, found {found}")
    return classes[0]


def _is_catalog(path: Path) -> bool:
    return (path / "registry.json").is_file()


def find_catalog(
    explicit: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    search_from: str | Path | None = None,
) -> Path:
    """Return the catalog root, or raise ``CatalogError`` saying how to point at one."""
    environ = os.environ if environ is None else environ
    if explicit is not None:
        if not _is_catalog(Path(explicit)):
            raise CatalogError(f"{explicit} is not a honba-strategies catalog (no registry.json)")
        return Path(explicit).resolve()
    env = environ.get(CATALOG_ENV, "").strip()
    if env:
        if not _is_catalog(Path(env)):
            raise CatalogError(f"${CATALOG_ENV}={env} is not a catalog (no registry.json)")
        return Path(env).resolve()
    if search_from is not None:
        start = Path(search_from).resolve()
        for base in (start, *start.parents):
            candidate = base.parent / CATALOG_DIRNAME
            if _is_catalog(candidate):
                return candidate.resolve()
    raise CatalogError(f"honba-strategies catalog not found: pass its path or set ${CATALOG_ENV}.")


def _registry_entries(catalog: Path) -> dict[str, str]:
    """``{name: relative path}`` from the top-level and per-strategy registries."""
    entries: dict[str, str] = {}
    top = json.loads((catalog / "registry.json").read_text(encoding="utf-8"))
    for e in top.get("strategies", []):
        entries.setdefault(e["name"], e["path"])
    for reg in sorted(catalog.glob("**/registry.json")):
        if reg.parent == catalog:
            continue
        e = json.loads(reg.read_text(encoding="utf-8"))
        if isinstance(e, dict) and "name" in e:
            entries.setdefault(e["name"], e.get("path") or str(reg.parent.relative_to(catalog)))
    return entries


def load_catalog_strategy(name: str, catalog: str | Path) -> CatalogStrategy:
    """Import the strategy registered as ``name`` and read its ``config.toml``.

    The strategy directory, its two parents and the catalog root go on ``sys.path``
    (the catalog's own test convention), so sibling imports inside the catalog resolve.
    """
    catalog = Path(catalog).resolve()
    entries = _registry_entries(catalog)
    if name not in entries:
        raise CatalogError(
            f"no strategy named {name!r} in {catalog}; available: {', '.join(sorted(entries))}"
        )
    strategy_dir = (catalog / entries[name]).resolve()
    for p in (strategy_dir, strategy_dir.parent, strategy_dir.parent.parent, catalog):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    module = _import_file(strategy_dir / "strategy.py", f"honba_catalog_{name}")
    classes = _strategy_classes(module)
    named = [c for c in classes if getattr(c, "name", None) == name]
    if len(named) == 1:
        cls = named[0]
    elif len(classes) == 1:
        cls = classes[0]
    else:
        raise CatalogError(f"{strategy_dir / 'strategy.py'}: expected one Strategy named {name!r}")
    digest = hashlib.sha256()
    for f in ("strategy.py", "config.toml"):
        digest.update((strategy_dir / f).read_bytes())
    return CatalogStrategy(
        name=name,
        path=strategy_dir,
        cls=cls,
        config=StrategyConfig.from_toml(strategy_dir / "config.toml"),
        module=module,
        source_sha256=digest.hexdigest(),
    )
