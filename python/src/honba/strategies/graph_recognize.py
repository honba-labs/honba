"""Code -> graph recognition, the inverse of :mod:`honba.strategies.graph_codegen` (E9-S3).

``recognize(source)`` recovers a :class:`~honba.strategies.graph.StrategyGraph` from Python
source. It accepts exactly the generated subset — the ``# honba:strategy-graph:v1`` marker and
the ``GRAPH = _graph.StrategyGraph.build(...)`` builder chain — plus the documented idiom of a
generated module with hand-written additions.

The builder chain is the lossless core: ``recognize(generate(graph)).graph == graph`` for every
graph (property-tested over the indicator catalogue). Anything the graph does not capture is
reported as an *unsupported span* — a line range with a reason — never silently dropped. Source
with no builder block is marked ``code_only`` (there is no graph to recover), with the whole file
as one unsupported span, so a round trip is never lossy.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from honba.strategies.graph import StrategyGraph
from honba.strategies.graph_codegen import CLASS_MARKER, MARKER

__all__ = ["RecognitionResult", "Span", "recognize"]

_BUILDER_METHODS = ("node", "edge", "with_subscriptions")


@dataclass(frozen=True, slots=True)
class Span:
    """A line range (1-based, inclusive) the graph does not capture, and why."""

    start: int
    end: int
    reason: str

    def __str__(self) -> str:
        return f"{self.start}-{self.end}: {self.reason}"


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """What ``recognize`` recovered: the graph (if any) and the code it could not capture."""

    graph: StrategyGraph | None
    code_only: bool
    unsupported: tuple[Span, ...] = ()

    @property
    def ok(self) -> bool:
        """A graph was recovered and no code was left uncaptured."""
        return self.graph is not None and not self.unsupported


class _NotSupported(Exception):
    """The builder expression is not one we can read losslessly."""


def recognize(source: str) -> RecognitionResult:
    """Recover the graph from ``source`` (see the module docstring)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        lineno = exc.lineno or 1
        return RecognitionResult(None, True, (Span(lineno, lineno, "syntax_error"),))

    lines = source.splitlines()
    has_marker = any(MARKER in line for line in lines)
    graph = _parse_builder(tree) if has_marker else None
    if graph is None:
        last = len(lines) or 1
        return RecognitionResult(None, True, (Span(1, last, "no strategy graph builder block"),))

    supported: list[ast.stmt] = []
    for stmt in tree.body:
        if _is_generated_statement(stmt, lines):
            supported.append(stmt)
    unsupported: list[Span] = []
    for stmt in tree.body:
        if stmt in supported:
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # module docstring
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        unsupported.append(
            Span(stmt.lineno, getattr(stmt, "end_lineno", stmt.lineno), _reason(stmt))
        )
    return RecognitionResult(graph, False, tuple(unsupported))


def _reason(stmt: ast.stmt) -> str:
    if isinstance(stmt, ast.ClassDef):
        return f"hand-written class {stmt.name!r} is not part of the generated subset"
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"function {stmt.name!r} is not captured by the graph"
    return f"statement {type(stmt).__name__} is not captured by the graph"


_GENERATED_ASSIGNMENTS = ("GRAPH", "GRAPH_HASH")


def _is_generated_statement(stmt: ast.stmt, lines: list[str]) -> bool:
    if isinstance(stmt, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id in _GENERATED_ASSIGNMENTS for t in stmt.targets)
    if isinstance(stmt, ast.ClassDef):
        # The generated class is preceded by the class marker comment.
        above = stmt.lineno - 2
        return 0 <= above < len(lines) and CLASS_MARKER in lines[above]
    return False


def _parse_builder(tree: ast.Module) -> StrategyGraph | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "GRAPH" for t in stmt.targets
        ):
            try:
                return _eval_builder(stmt.value)
            except _NotSupported:
                return None
    return None


def _eval_builder(expr: ast.expr) -> StrategyGraph:
    steps: list[ast.Call] = []
    while (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr in _BUILDER_METHODS
    ):
        steps.append(expr)
        expr = expr.func.value
    steps.reverse()
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Attribute):
        raise _NotSupported
    base = expr.func
    assert isinstance(base, ast.Attribute)
    if base.attr != "build":
        raise _NotSupported
    base_kwargs = _literal_kwargs(expr)
    try:
        graph = StrategyGraph.build(**base_kwargs)
    except Exception as exc:  # pydantic validation of the base call
        raise _NotSupported(str(exc)) from exc
    for step in steps:
        func = step.func
        if not isinstance(func, ast.Attribute):
            raise _NotSupported
        method = func.attr
        kwargs = _literal_kwargs(step)
        try:
            if method == "node":
                graph.node(**kwargs)
            elif method == "edge":
                graph.edge(**kwargs)
            elif method == "with_subscriptions":
                graph.with_subscriptions(**kwargs)
        except Exception as exc:
            raise _NotSupported(str(exc)) from exc
    return graph


def _literal_kwargs(call: ast.Call) -> dict[str, Any]:
    if call.args:
        raise _NotSupported("builder calls take keyword arguments only")
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise _NotSupported("no **kwargs in the builder chain")
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except ValueError as exc:
            raise _NotSupported(f"non-literal argument {kw.arg!r}") from exc
    return out
