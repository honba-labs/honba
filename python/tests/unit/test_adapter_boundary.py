"""Unit tests for the adapter boundary rule (E1-S1).

The rule is one line: no module outside an adapter package may import a broker SDK or another
adapter package. These tests drive the checker with synthetic trees, so each case says exactly
which import shape it is about.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from honba.adapters.boundary import (
    DEFAULT_FORBIDDEN,
    BoundaryViolation,
    find_boundary_violations,
    format_violations,
)


def write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


class TestForbiddenImports:
    def test_a_clean_tree_has_no_violations(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "engine/loop.py",
            "from honba.domain import Bar\nimport pandas\nfrom . import sibling\n",
        )
        assert find_boundary_violations(tmp_path) == []

    def test_catches_a_broker_sdk_import(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "from dhanhq import dhanhq\n")
        violations = find_boundary_violations(tmp_path)
        assert [v.module for v in violations] == ["dhanhq"]
        assert violations[0].reason == "a broker SDK"

    def test_catches_a_broker_adapter_package_import(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "from honba_dhan import DhanAdapter\n")
        violations = find_boundary_violations(tmp_path)
        assert violations[0].reason == "a broker adapter package"

    def test_catches_the_shared_adapter_helpers(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "from honba_adapters_shared.rate_limit import Bucket\n")
        assert len(find_boundary_violations(tmp_path)) == 1

    def test_matches_sdk_names_regardless_of_case(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/a.py", "import SmartApi\n")
        write(tmp_path, "engine/b.py", "from SmartApi.angelconnect import SmartConnect\n")
        write(tmp_path, "engine/c.py", "import AngelOne\n")
        assert len(find_boundary_violations(tmp_path)) == 3

    def test_catches_a_plain_import_of_a_forbidden_package(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "import zerodha\n")
        assert len(find_boundary_violations(tmp_path)) == 1

    def test_a_relative_import_is_never_a_boundary_violation(self, tmp_path: Path) -> None:
        write(tmp_path, "adapter/parsing.py", "from .zerodha_wire import parse\n")
        assert find_boundary_violations(tmp_path) == []

    def test_a_module_that_merely_mentions_a_broker_is_fine(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", 'BROKER = "dhanhq"\n# import dhanhq in a docstring\n')
        assert find_boundary_violations(tmp_path) == []

    def test_reports_the_line_number(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "import pandas\n\nimport fyers\n")
        assert find_boundary_violations(tmp_path)[0].line == 3

    def test_finds_violations_in_every_subtree(self, tmp_path: Path) -> None:
        write(tmp_path, "a/one.py", "import upstox\n")
        write(tmp_path, "a/b/two.py", "from upstox import Upstox\n")
        assert len(find_boundary_violations(tmp_path)) == 2

    def test_skips_caches_and_build_output(self, tmp_path: Path) -> None:
        write(tmp_path, "__pycache__/engine.py", "import upstox\n")
        write(tmp_path, "build/lib/engine.py", "import upstox\n")
        assert find_boundary_violations(tmp_path) == []


class TestAdapterRootsAreExempt:
    def test_an_adapter_package_may_import_its_own_sdk(self, tmp_path: Path) -> None:
        write(
            tmp_path, "zerodha/src/honba_zerodha/http.py", "from kiteconnect import KiteConnect\n"
        )
        write(tmp_path, "honba/src/honba/engine.py", "import pandas\n")
        adapter_root = tmp_path / "zerodha"
        assert find_boundary_violations(tmp_path, adapter_roots=[adapter_root]) == []

    def test_a_sibling_adapter_package_is_still_forbidden_outside_the_exemption(
        self, tmp_path: Path
    ) -> None:
        write(
            tmp_path, "zerodha/src/honba_zerodha/http.py", "from kiteconnect import KiteConnect\n"
        )
        write(tmp_path, "honba/src/honba/engine.py", "import honba_dhan\n")
        violations = find_boundary_violations(tmp_path, adapter_roots=[tmp_path / "zerodha"])
        assert [v.module for v in violations] == ["honba_dhan"]

    def test_the_exemption_covers_nested_files(self, tmp_path: Path) -> None:
        write(tmp_path, "adapter/a/b/c/deep.py", "import zerodha\n")
        assert find_boundary_violations(tmp_path, adapter_roots=[tmp_path / "adapter"]) == []


class TestCustomForbiddenSets:
    def test_a_repo_can_forbid_something_else_too(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "import requests\n")
        assert find_boundary_violations(tmp_path, forbidden=frozenset({"requests"}))
        assert find_boundary_violations(tmp_path) == []


class TestReporting:
    def test_a_violation_renders_with_path_line_and_reason(self, tmp_path: Path) -> None:
        write(tmp_path, "engine/loop.py", "import dhanhq\n")
        rendered = format_violations(find_boundary_violations(tmp_path))
        assert "engine/loop.py:1" in rendered
        assert "dhanhq" in rendered
        assert "a broker SDK" in rendered

    def test_formatting_nothing_is_empty(self) -> None:
        assert format_violations([]) == ""

    def test_a_syntax_error_is_reported_rather_than_crashing_the_check(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "engine/broken.py", "def (:\n")
        violations = find_boundary_violations(tmp_path)
        assert violations[0].module == "<unparsed>"
        assert "syntax error" in violations[0].reason

    def test_violation_is_a_frozen_value(self, tmp_path: Path) -> None:
        violation = BoundaryViolation(Path("a.py"), 1, "dhanhq", "a broker SDK")
        with pytest.raises(AttributeError):
            violation.line = 2  # type: ignore[misc]


def test_the_default_forbidden_set_covers_every_workspace_adapter() -> None:
    assert {
        "honba_dhan",
        "honba_zerodha",
        "honba_fyers",
        "honba_upstox",
        "honba_angelone",
        "honba_kotak",
        "honba_iifl",
        "honba_motilal",
        "honba_adapters_shared",
    } <= DEFAULT_FORBIDDEN
