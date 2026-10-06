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
    source_sha256: str  # sha256 of the whole strategy directory (see _directory_sha256)

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


_SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "site-packages", "build", "dist"})


def _scannable(relative: Path) -> bool:
    """False for hidden (``.git``, ``.venv``) and vendor directories anywhere in the path."""
    return not any(part.startswith(".") or part in _SKIPPED_DIRS for part in relative.parts)


def _read_registry(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"unreadable registry {path}: {exc}") from exc


def _registry_entries(catalog: Path) -> dict[str, str]:
    """``{name: relative path}`` from the top-level and per-strategy registries.

    The registry is untrusted content: a malformed one raises :class:`CatalogError`.
    """
    entries: dict[str, str] = {}
    top_path = catalog / "registry.json"
    top = _read_registry(top_path)
    if not isinstance(top, dict):
        raise CatalogError(f"malformed registry {top_path}: expected a JSON object")
    listed = top.get("strategies", [])
    if not isinstance(listed, list):
        raise CatalogError(f"malformed registry {top_path}: 'strategies' must be a list")
    for e in listed:
        if not (
            isinstance(e, dict)
            and isinstance(e.get("name"), str)
            and isinstance(e.get("path"), str)
        ):
            raise CatalogError(
                f"malformed registry {top_path}: each strategy needs a string 'name' and 'path', "
                f"got {e!r}"
            )
        entries.setdefault(e["name"], e["path"])
    for reg in sorted(catalog.glob("**/registry.json")):
        if reg.parent == catalog or not _scannable(reg.parent.relative_to(catalog)):
            continue
        e = _read_registry(reg)
        if isinstance(e, dict) and isinstance(e.get("name"), str):
            path = e.get("path")
            if path is not None and not isinstance(path, str):
                raise CatalogError(f"malformed registry {reg}: 'path' must be a string")
            entries.setdefault(e["name"], path or str(reg.parent.relative_to(catalog)))
    return entries


def _directory_sha256(root: Path) -> str:
    """Deterministic digest of every file under ``root`` (run provenance).

    Files are visited in sorted relative-path order; each contributes its length-prefixed
    POSIX relative path and length-prefixed bytes, so file boundaries cannot be confused.
    Hidden files and directories and ``__pycache__`` are ignored.
    """
    digest = hashlib.sha256()
    files = sorted(
        (p.relative_to(root) for p in root.rglob("*") if p.is_file()),
        key=lambda r: r.as_posix(),
    )
    for rel in files:
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        name = rel.as_posix().encode()
        data = (root / rel).read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def _load_module_isolated(strategy_dir: Path, catalog: Path, module_name: str) -> ModuleType:
    """Import ``strategy.py`` with its directory chain on ``sys.path``, then undo the leaks.

    The strategy directory, its two parents and the catalog root are on ``sys.path`` only
    while importing (the catalog's own test convention), so sibling imports resolve. After
    the import ``sys.path`` is restored and every module the strategy pulled in from its
    own directory (``helpers.py`` beside it) is dropped from ``sys.modules``, so two
    strategies that each have a ``helpers.py`` never see each other's. Imports a strategy
    defers to call time will not find its siblings.
    """
    saved_path = list(sys.path)
    before = set(sys.modules)
    for p in (catalog, strategy_dir.parent.parent, strategy_dir.parent, strategy_dir):
        sys.path.insert(0, str(p))
    try:
        return _import_file(strategy_dir / "strategy.py", module_name)
    finally:
        sys.path[:] = saved_path
        for mod_name in set(sys.modules) - before - {module_name}:
            mod_file = getattr(sys.modules[mod_name], "__file__", None)
            if mod_file and Path(mod_file).resolve().is_relative_to(strategy_dir):
                del sys.modules[mod_name]


def load_catalog_strategy(name: str, catalog: str | Path) -> CatalogStrategy:
    """Import the strategy registered as ``name`` and read its ``config.toml``.

    The strategy directory, its two parents and the catalog root are put on ``sys.path`` while importing
    (see :func:`_load_module_isolated`). A registry path that resolves outside the
    catalog is refused.
    """
    catalog = Path(catalog).resolve()
    entries = _registry_entries(catalog)
    if name not in entries:
        raise CatalogError(
            f"no strategy named {name!r} in {catalog}; available: {', '.join(sorted(entries))}"
        )
    strategy_dir = (catalog / entries[name]).resolve()
    if not strategy_dir.is_relative_to(catalog):
        raise CatalogError(
            f"strategy {name!r}: path {entries[name]!r} resolves outside the catalog {catalog}"
        )
    if not (strategy_dir / "strategy.py").is_file():
        raise CatalogError(f"strategy {name!r}: {strategy_dir / 'strategy.py'} not found")
    module = _load_module_isolated(strategy_dir, catalog, f"honba_catalog_{name}")
    classes = _strategy_classes(module)
    named = [c for c in classes if getattr(c, "name", None) == name]
    if len(named) == 1:
        cls = named[0]
    elif len(classes) == 1:
        cls = classes[0]
    else:
        raise CatalogError(f"{strategy_dir / 'strategy.py'}: expected one Strategy named {name!r}")
    return CatalogStrategy(
        name=name,
        path=strategy_dir,
        cls=cls,
        config=StrategyConfig.from_toml(strategy_dir / "config.toml"),
        module=module,
        source_sha256=_directory_sha256(strategy_dir),
    )
