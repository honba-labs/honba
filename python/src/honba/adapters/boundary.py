"""The adapter boundary rule, enforced in Python (E1-S1).

One rule: **broker wire types never appear outside an adapter implementation**. Concretely, no
module outside an adapter package may import a broker SDK or another adapter package. A broker
SDK type that reaches the engine is not a leaky abstraction, it is a bug with a stack trace
pointing the wrong way.

The rule is mechanical, so it is checked mechanically rather than by convention: parse the
imports, compare the top-level module against a forbidden set. It is deliberately a plain
stdlib checker with no dependencies, because it must run in CI for the core repo, inside the
adapter repo, and in any adapter's own suite.

    from honba.adapters.boundary import find_boundary_violations

    violations = find_boundary_violations(Path("python/src/honba"))

Inside an adapter package nothing is forbidden: that is where the broker's vocabulary belongs,
and translating it is the adapter's job. The forbidden set is a floor, not a ceiling — an
adapter that needs something else forbidden can pass ``forbidden=``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "BROKER_ADAPTER_PACKAGES",
    "BROKER_SDK_MODULES",
    "DEFAULT_FORBIDDEN",
    "BoundaryViolation",
    "find_boundary_violations",
    "format_violations",
]

#: Sibling packages under ``honba-adapters``. Core must not import any of them; they are
#: installed separately and would drag broker dependencies into the engine.
BROKER_ADAPTER_PACKAGES: frozenset[str] = frozenset(
    {
        "honba_adapters_shared",
        "honba_angelone",
        "honba_dhan",
        "honba_fyers",
        "honba_iifl",
        "honba_kotak",
        "honba_motilal",
        "honba_upstox",
        "honba_zerodha",
    }
)

#: Broker SDK top-level module names. Lower-cased for matching, because vendors are
#: inconsistent about case (``SmartApi``, ``angel_one``, ``kotakneo``).
BROKER_SDK_MODULES: frozenset[str] = frozenset(
    {
        "angel_one",
        "angelone",
        "dhanhq",
        "fyers",
        "fyers_api",
        "kotak",
        "kotak_neo",
        "kotakneo",
        "motilal",
        "smartapi",
        "upstox",
        "xtension",
        "zerodha",
        "zerodha_kite",
    }
)

#: Everything a non-adapter module may not import.
DEFAULT_FORBIDDEN: frozenset[str] = BROKER_ADAPTER_PACKAGES | BROKER_SDK_MODULES

_SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist"}
)


@dataclass(frozen=True, slots=True)
class BoundaryViolation:
    """One import that breaks the boundary rule."""

    path: Path
    line: int
    module: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: imports {self.module} ({self.reason})"


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _imported_modules(tree: ast.AST) -> Iterable[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def find_boundary_violations(
    root: Path,
    *,
    forbidden: frozenset[str] = DEFAULT_FORBIDDEN,
    adapter_roots: Iterable[Path] = (),
) -> list[BoundaryViolation]:
    """Every forbidden import under ``root``, sorted by path then line.

    ``adapter_roots`` are exempt: inside them the broker's own vocabulary is the point. Roots
    are compared after resolving, so a relative path from the caller's working directory works.
    """
    exempt = {Path(path).resolve() for path in adapter_roots}
    lowered = {name.lower() for name in forbidden}
    root = Path(root)
    violations: list[BoundaryViolation] = []
    for path in sorted(root.rglob("*.py")):
        if _SKIP_DIRS & set(path.parts):
            continue
        resolved = path.resolve()
        if any(resolved == exempt_dir or exempt_dir in resolved.parents for exempt_dir in exempt):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            violations.append(
                BoundaryViolation(path, error.lineno or 0, "<unparsed>", f"syntax error: {error}")
            )
            continue
        for line, module in _imported_modules(tree):
            top = _top_level(module)
            if top not in forbidden and top.lower() not in lowered:
                continue
            reason = (
                "a broker adapter package" if top in BROKER_ADAPTER_PACKAGES else "a broker SDK"
            )
            violations.append(BoundaryViolation(path, line, module, reason))
    return violations


def format_violations(violations: Iterable[BoundaryViolation]) -> str:
    """One line per violation, for a test failure or a CI log."""
    return "\n".join(str(violation) for violation in violations)
