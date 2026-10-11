"""Unit tests for the AI strategy verifier static scan (E5-S5).

The sandboxed execution half (subprocess, rlimits, audit hook) is integration-tested in
``tests/integration/test_strategy_verifier_flow.py``; these tests are pure and fast and cover the
AST import allowlist, the escape denylist and the short-circuit behaviour of :func:`verify_source`.
"""

from __future__ import annotations

import pytest

from honba.ai.verification import Finding, FindingCode, scan_source, verify_source

GOOD_SOURCE = """
from __future__ import annotations

import math
from collections import deque

import numpy as np

from honba.entities.bar import Bar
from honba.strategies.base import Parameter, Strategy
from honba.strategies.config import StrategyConfig


class Good(Strategy):
    name = "good"
    period = Parameter(10, min=2, max=50, step=1)

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self._closes: deque[float] = deque(maxlen=50)

    def on_bar(self, bar: Bar) -> None:
        self._closes.append(bar.close)
        if len(self._closes) == 50 and not self.busy(bar.instrument_id):
            mean = sum(self._closes) / len(self._closes)
            if np.isfinite(mean) and bar.close < mean:
                self.buy(bar.instrument_id, 1.0)
"""


def _codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


class TestScanSource:
    def test_ast_rejects_forbidden_import(self) -> None:
        findings = scan_source("import os\nimport subprocess\nfrom socket import socket\n")
        assert _codes(findings) == [FindingCode.FORBIDDEN_IMPORT] * 3
        assert [f.message for f in findings] == [
            "import 'os' is not on the strategy allowlist",
            "import 'subprocess' is not on the strategy allowlist",
            "import 'socket' is not on the strategy allowlist",
        ]

    def test_imports_of_allowed_modules_pass(self) -> None:
        assert scan_source(GOOD_SOURCE) == []

    def test_relative_and_nested_allowed_imports_pass(self) -> None:
        assert scan_source("from honba.strategies.indicators import Sma\n") == []
        assert scan_source("import honba.strategies.sizing as s\n") == []

    @pytest.mark.parametrize(
        "module",
        [
            "os",
            "sys",
            "subprocess",
            "socket",
            "ctypes",
            "importlib",
            "builtins",
            "shutil",
            "pathlib",
            "io",
            "resource",
            "multiprocessing",
            "threading",
            "http",
            "urllib.request",
            "requests",
            "httpx",
            "pickle",
            "marshal",
            "time",
            "datetime",
        ],
    )
    def test_rejects_each_dangerous_module(self, module: str) -> None:
        findings = scan_source(f"import {module}\n")
        assert _codes(findings) == [FindingCode.FORBIDDEN_IMPORT]

    @pytest.mark.parametrize(
        "expression",
        [
            "eval('1 + 1')",
            "exec('x = 1')",
            "compile('1', '<s>', 'eval')",
            "__import__('os')",
            "open('/etc/passwd')",
            "input()",
            "globals()",
            "locals()",
            "vars()",
            "getattr(object, '__globals__')",
            "setattr(widget, 'x', 1)",
            "delattr(widget, 'x')",
            "type(widget)",
        ],
    )
    def test_rejects_forbidden_builtin_call(self, expression: str) -> None:
        findings = scan_source(f"value = {expression}\n")
        assert _codes(findings) == [FindingCode.FORBIDDEN_NAME]

    @pytest.mark.parametrize(
        "attribute",
        [
            "__globals__",
            "__subclasses__",
            "__bases__",
            "__mro__",
            "__builtins__",
            "__code__",
            "__class__",
            "__dict__",
            "__reduce__",
            "__self__",
            "tb_frame",
        ],
    )
    def test_rejects_dunder_escape_attribute(self, attribute: str) -> None:
        findings = scan_source(f"escape = object.{attribute}\n")
        assert _codes(findings) == [FindingCode.FORBIDDEN_DUNDER]

    def test_dunder_climb_chain_is_fully_flagged(self) -> None:
        findings = scan_source("x = ().__class__.__mro__\n")
        assert _codes(findings) == [FindingCode.FORBIDDEN_DUNDER, FindingCode.FORBIDDEN_DUNDER]

    @pytest.mark.parametrize(
        "module",
        [
            "honba.strategies.loader",
            "honba.data.loaders.nse",
            "honba.ai.verification",
            "honba.adapters.base",
            "honba.ai.mcp",
        ],
    )
    def test_rejects_honba_submodules_outside_the_vetted_set(self, module: str) -> None:
        findings = scan_source(f"import {module}\n")
        assert _codes(findings) == [FindingCode.FORBIDDEN_IMPORT]

    @pytest.mark.parametrize(
        "statement",
        [
            "import honba.entities.bar",
            "import honba.strategies.sizing",
            "from honba.strategies.base import Strategy",
            "from honba.strategies.config import StrategyConfig",
            "from honba.strategies.indicators import Sma",
        ],
    )
    def test_vetted_honba_submodules_import(self, statement: str) -> None:
        assert scan_source(statement + "\n") == []

    def test_reports_syntax_error_with_location(self) -> None:
        findings = scan_source("def broken(:\n    pass\n")
        assert _codes(findings) == [FindingCode.SYNTAX_ERROR]
        assert findings[0].lineno == 1

    def test_findings_carry_line_and_column(self) -> None:
        findings = scan_source("\n\nimport os\n")
        assert len(findings) == 1
        assert findings[0].lineno == 3
        assert findings[0].col is not None

    def test_finding_codes_are_stable_strings(self) -> None:
        assert FindingCode.FORBIDDEN_IMPORT == "forbidden_import"
        assert FindingCode.FORBIDDEN_NAME == "forbidden_name"
        assert FindingCode.FORBIDDEN_DUNDER == "forbidden_dunder"
        assert FindingCode.SYNTAX_ERROR == "syntax_error"


class TestVerifySourceShortCircuit:
    def test_static_violation_is_not_executed(self) -> None:
        report = verify_source("import os\n")
        assert report.ok is False
        assert report.sandbox is None
        assert _codes(list(report.static)) == [FindingCode.FORBIDDEN_IMPORT]

    def test_syntax_error_is_not_executed(self) -> None:
        report = verify_source("def broken(:\n")
        assert report.ok is False
        assert report.sandbox is None
        assert _codes(list(report.static)) == [FindingCode.SYNTAX_ERROR]

    def test_findings_aggregates_static_and_sandbox(self) -> None:
        report = verify_source("import os\n")
        assert list(report.findings) == list(report.static)
