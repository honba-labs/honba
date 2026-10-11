"""AST static scan for untrusted strategy source (E5-S5).

The security boundary is an **import allowlist**: a strategy may import only the pure-stdlib
modules it needs to compute, the numeric stack, and ``honba`` itself. Everything else — operating
system, process, network, filesystem, serialization, introspection — is refused by omission. A
second, smaller denylist catches the escapes that need no import: the dangerous builtins
(``eval``/``exec``/``__import__``/``open``) and the dunder attributes used to walk from a value to
its module machinery.

This is pure analysis: parse the source, never execute it. It decides *safety*, not correctness;
the sandboxed smoke run decides whether the strategy actually works. The shape mirrors the adapter
boundary checker (``honba/adapters/boundary.py``), with the strategy threat model instead of a
broker SDK one.
"""

from __future__ import annotations

import ast

from honba.ai.verification.findings import Finding, FindingCode

__all__ = [
    "ALLOWED_IMPORTS",
    "FORBIDDEN_DUNDER_ATTRIBUTES",
    "FORBIDDEN_NAMES",
    "HONBA_MODULES",
    "scan_source",
]

#: Top-level modules a verified strategy may import. ``time``/``datetime`` are deliberately
#: absent: a verifiable strategy is wall-clock free. ``honba`` is not listed because its imports
#: are vetted submodule-by-submodule (:data:`HONBA_MODULES`).
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "__future__",
        "abc",
        "array",
        "bisect",
        "collections",
        "copy",
        "dataclasses",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "json",
        "math",
        "numbers",
        "operator",
        "random",
        "re",
        "statistics",
        "string",
        "typing",
        "typing_extensions",
        # numeric stack
        "numpy",
        "pandas",
    }
)

#: ``honba`` submodules a strategy may import, vetted for what they make reachable. The loader,
#: verification, ``ai``, adapters and ``data`` packages are deliberately excluded: they pull in
#: ``os``/``sys``/``importlib``/HTTP clients as attributes and would silently re-open the escapes
#: the top-level deny list closes.
HONBA_MODULES: frozenset[str] = frozenset(
    {
        "honba.domain",
        "honba.entities",
        "honba.strategies.base",
        "honba.strategies.config",
        "honba.strategies.indicators",
        "honba.strategies.manifest",
        "honba.strategies.sizing",
        "honba.backtest",
    }
)

#: Builtins that let a strategy execute arbitrary code, reach the interpreter's globals, or climb
#: from a value through ``getattr`` (which would otherwise bypass the dunder denylist below).
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "quit",
        "setattr",
        "type",
        "vars",
        "__import__",
    }
)

#: Attributes used to climb from a value to modules, frames or the interpreter.
FORBIDDEN_DUNDER_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__bases__",
        "__builtins__",
        "__class__",
        "__closure__",
        "__code__",
        "__dict__",
        "__func__",
        "__getattribute__",
        "__globals__",
        "__import__",
        "__loader__",
        "__mro__",
        "__reduce__",
        "__reduce_ex__",
        "__self__",
        "__subclasses__",
        "__wrapped__",
        "cr_frame",
        "f_globals",
        "f_locals",
        "gi_frame",
        "tb_frame",
    }
)


def _is_allowed_module(full: str) -> bool:
    if full in {"honba"}:
        return True
    if full.startswith("honba."):
        return any(full == module or full.startswith(module + ".") for module in HONBA_MODULES)
    return full.split(".")[0] in ALLOWED_IMPORTS


def _module_of_import(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name
    if node.level:
        return None  # relative import: package-local, not a top-level module escape
    return node.module


def _callee_name(func: ast.expr) -> str | None:
    # The forbidden list is bare builtins. Match only ``ast.Name`` so a method like
    # ``df.eval(...)`` or ``obj.compile(...)`` is not a false positive.
    if isinstance(func, ast.Name):
        return func.id
    return None


def scan_source(source: str, *, filename: str = "<strategy>") -> list[Finding]:
    """Parse ``source`` and return every static violation, ordered by source position.

    An unparsable source yields a single ``syntax_error`` finding. A clean source yields ``[]``.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [
            Finding(
                FindingCode.SYNTAX_ERROR,
                exc.msg or "invalid syntax",
                exc.lineno,
                exc.offset,
            )
        ]

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            module = _module_of_import(node)
            if module is not None and not _is_allowed_module(module):
                findings.append(
                    Finding(
                        FindingCode.FORBIDDEN_IMPORT,
                        f"import {module!r} is not on the strategy allowlist",
                        node.lineno,
                        node.col_offset,
                    )
                )
        elif isinstance(node, ast.Call):
            name = _callee_name(node.func)
            if name in FORBIDDEN_NAMES:
                findings.append(
                    Finding(
                        FindingCode.FORBIDDEN_NAME,
                        f"call to {name!r} is not allowed in a strategy",
                        node.lineno,
                        node.col_offset,
                    )
                )
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_DUNDER_ATTRIBUTES:
                findings.append(
                    Finding(
                        FindingCode.FORBIDDEN_DUNDER,
                        f"access to {node.attr!r} is not allowed in a strategy",
                        node.lineno,
                        node.col_offset,
                    )
                )

    findings.sort(key=lambda f: (f.lineno or 0, f.col or 0))
    return findings
