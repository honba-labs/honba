"""ADR 0012: every version axis has exactly one owner, and it is Rust.

Python reads ``SCHEMA_VERSION``, ``API_VERSION`` and ``STRATEGY_API_VERSION`` from
``honba._honba`` at import time; a second literal in Python is how two owners drift.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_honba = pytest.importorskip("honba._honba")

SRC = Path(__file__).resolve().parents[2] / "src" / "honba"
VERSION_NAMES = {"SCHEMA_VERSION", "API_VERSION", "STRATEGY_API_VERSION"}


def test_wire_versions_are_the_rust_values() -> None:
    from honba import wire

    assert wire.SCHEMA_VERSION == _honba.SCHEMA_VERSION
    assert wire.API_VERSION == _honba.API_VERSION
    assert isinstance(_honba.SCHEMA_VERSION, int)
    assert isinstance(_honba.API_VERSION, str) and _honba.API_VERSION.count(".") == 2


def test_strategy_api_version_is_the_rust_value() -> None:
    from honba.strategies import manifest

    assert isinstance(_honba.STRATEGY_API_VERSION, str)
    assert manifest.STRATEGY_API_VERSION == _honba.STRATEGY_API_VERSION


def _literal_version_assignments(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if names & VERSION_NAMES and isinstance(value, ast.Constant):
            found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return found


def test_no_python_module_redeclares_a_version_literal() -> None:
    offenders = [
        hit
        for path in sorted(SRC.rglob("*.py"))
        if "_generated" not in path.parts
        for hit in _literal_version_assignments(path)
    ]
    assert offenders == []
