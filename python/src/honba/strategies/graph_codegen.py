"""Deterministic graph -> Python code generation (E9-S2, ADR 0029).

``generate(graph)`` turns a :class:`~honba.strategies.graph.StrategyGraph` into readable,
runnable Python:

* a **builder block** — a ``GRAPH = _graph.StrategyGraph.build(...)`` chain of
  ``.node(...)`` / ``.edge(...)`` calls. This is the lossless, machine-readable core: the
  recogniser in :mod:`honba.strategies.graph_recognize` reads it back to recover the exact
  graph (round-trip identity).
* a **strategy class** — a ``Strategy`` subclass with ``Parameter`` declarations (one line
  each) and an ``on_bar`` that updates the graph's indicators and submits orders whose
  ``reason`` strings carry the node labels.

Generation is deterministic: nodes and edges keep their order, names are stable functions of
node ids, and the canonical JSON that hashes to ``GRAPH_HASH`` is embedded verbatim in the
builder block, so two runs of ``generate`` on the same graph are byte-identical.

The executable subset is the decision pipeline ``universe -> indicator(s) -> condition /
logic -> sizing -> risk -> order``. A graph outside that subset still round-trips (the builder
block is always complete); the generated ``on_bar`` simply notes the nodes it cannot lower to
code as comments, never dropping them from ``GRAPH``.
"""

from __future__ import annotations

import keyword
import re
from typing import Any

from honba.strategies.graph import GraphNode, StrategyGraph

__all__ = ["CLASS_MARKER", "MARKER", "generate"]

MARKER = "# honba:strategy-graph:v1"
"""The header comment that marks a module generated from a StrategyGraph."""

CLASS_MARKER = "# honba:strategy-graph:generated"
"""Marks the generated strategy class, so the recogniser treats it as part of the subset."""

_BUILDER_METHODS = ("node", "edge")


def _class_name(name: str) -> str:
    parts = re.split(r"[^0-9a-zA-Z]+", name)
    ident = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not ident:
        ident = "StrategyGraph"
    if ident[0].isdigit():
        ident = "S" + ident
    return ident


def _ident(text: str) -> str:
    ident = re.sub(r"[^0-9a-zA-Z_]", "_", text)
    if not ident or ident[0].isdigit() or keyword.iskeyword(ident):
        ident = "n_" + ident
    return ident


def _lit(value: Any) -> str:
    # Python literal repr (not JSON): ``False``/``True``/``None`` are what ``ast.literal_eval``
    # reads back in the recogniser, and the values are always plain JSON-shaped scalars.
    return repr(value)


def _kwargs(pairs: list[tuple[str, Any]]) -> str:
    return ", ".join(f"{k}={_lit(v)}" for k, v in pairs)


def _topo_order(graph: StrategyGraph) -> list[str]:
    """Node ids in dependency order (edges from -> to); ties keep declaration order."""
    order = [n.id for n in graph.nodes]
    deps: dict[str, set[str]] = {nid: set() for nid in order}
    for e in graph.edges:
        if e.to_node in deps and e.from_node in deps:
            deps[e.to_node].add(e.from_node)
    result: list[str] = []
    while deps:
        ready = [nid for nid in order if nid in deps and not (deps[nid] & set(deps))]
        if not ready:  # cycle: fall back to declaration order
            ready = [nid for nid in order if nid in deps]
        for nid in ready:
            result.append(nid)
            deps.pop(nid)
    return result


def _indicator_instances(graph: StrategyGraph) -> list[GraphNode]:
    return [n for n in graph.nodes if n.type == "indicator"]


def _parameter_layout(graph: StrategyGraph) -> dict[tuple[str, str], str]:
    """(node_id, param) -> python attribute name; short names unless a clash forces a prefix."""
    counts: dict[str, int] = {}
    for node in _indicator_instances(graph):
        for pname in node.params:
            counts[pname] = counts.get(pname, 0) + 1
    layout: dict[tuple[str, str], str] = {}
    for node in _indicator_instances(graph):
        for pname in node.params:
            if counts[pname] > 1:
                layout[(node.id, pname)] = f"{_ident(node.id)}_{pname}"
            else:
                layout[(node.id, pname)] = _ident(pname)
    return layout


def _incoming(graph: StrategyGraph, node_id: str, port: str | None = None) -> list[tuple[str, str]]:
    return [
        (e.from_node, e.from_port)
        for e in graph.edges
        if e.to_node == node_id and (port is None or e.to_port == port)
    ]


def _generate_on_bar(graph: StrategyGraph, layout: dict[tuple[str, str], str]) -> list[str]:
    """The body of ``on_bar`` for the supported pipeline; comments mark the rest."""
    lines: list[str] = []
    by_id = {n.id: n for n in graph.nodes}
    prev_needed: set[str] = set()
    for node in graph.nodes:
        if node.type == "condition" and node.params.get("op") in ("crosses_above", "crosses_below"):
            prev_needed.add(node.id)

    for nid in _topo_order(graph):
        node = by_id[nid]
        local = _ident(nid)
        if node.type == "universe":
            continue
        if node.type == "indicator":
            lines.append(f"        {local} = self._{local}.update_bar(bar)")
        elif node.type == "benchmark":
            lines.append(f"        {local} = None  # benchmark series is not modelled yet")
        elif node.type == "condition":
            lines.extend(_condition_lines(graph, node, local, prev_needed))
        elif node.type == "logic":
            lines.extend(_logic_lines(graph, node, local))
        elif node.type == "sizing":
            lines.append(f"        {local} = {float(node.params.get('value', 1.0))!r}")
        elif node.type == "risk":
            src = _first_signal(graph, nid)
            lines.append(f"        {local} = {src or 'False'}  # risk gate: {node.display()!r}")
        elif node.type == "order":
            lines.extend(_order_lines(graph, node))
        elif node.type == "transform":
            src = _signal_or_series(graph, nid)
            lines.append(f"        {local} = {src}  # transform: {node.display()!r}")
        elif node.type == "schedule":
            lines.append(f"        {local} = True  # schedule: {node.display()!r}")
    if not lines:
        lines.append("        return")
    return lines


def _condition_lines(
    graph: StrategyGraph, node: GraphNode, local: str, prev_needed: set[str]
) -> list[str]:
    op = node.params.get("op", "gt")
    left = _first_port(graph, node.id, "left")
    right = _first_port(graph, node.id, "right")
    left_expr = _ident(left) if left else "bar.close"
    threshold = node.params.get("threshold")
    right_expr = (
        _ident(right) if right else repr(float(threshold if threshold is not None else 0.0))
    )
    if node.id in prev_needed:
        symbol = ">" if op == "crosses_above" else "<"
        return [
            (
                f"        {local}_now = bool({left_expr} is not None and {right_expr} is not None"
                f" and {left_expr} {symbol} {right_expr})"
            ),
            f"        {local}_was = getattr(self, '_{local}_was', False)",
            f"        {local} = bool({local}_now and not {local}_was)",
            f"        self._{local}_was = {local}_now",
        ]
    symbols = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=="}
    symbol = symbols.get(op, ">")
    return [
        (
            f"        {local} = bool({left_expr} is not None and {right_expr} is not None"
            f" and {left_expr} {symbol} {right_expr})"
        )
    ]


def _logic_lines(graph: StrategyGraph, node: GraphNode, local: str) -> list[str]:
    op = node.params.get("op", "and")
    a = _first_port(graph, node.id, "a") or "False"
    b = _first_port(graph, node.id, "b") or "False"
    a_expr, b_expr = _ident(a), _ident(b)
    if op == "or":
        expr = f"bool({a_expr} or {b_expr})"
    elif op == "not":
        expr = f"bool(not {a_expr})"
    else:
        expr = f"bool({a_expr} and {b_expr})"
    return [f"        {local} = {expr}"]


def _order_lines(graph: StrategyGraph, node: GraphNode) -> list[str]:
    side = str(node.params.get("side", "buy"))
    signal = _first_signal(graph, node.id) or "False"
    size = _first_size(graph, node.id)
    qty = _ident(size) if size else repr(float(node.params.get("quantity", 1.0)))
    label = node.label or ("Buy" if side == "buy" else "Sell")
    method = "buy" if side == "buy" else "sell"
    return [
        f"        if {signal}:",
        f"            self.{method}(bar.instrument_id, {qty}, reason={label!r})",
    ]


def _first_port(graph: StrategyGraph, node_id: str, port: str) -> str | None:
    for src, _ in _incoming(graph, node_id, port):
        return src
    return None


def _first_signal(graph: StrategyGraph, node_id: str) -> str | None:
    for src, _ in _incoming(graph, node_id):
        return _ident(src)
    return None


def _signal_or_series(graph: StrategyGraph, node_id: str) -> str:
    return _first_signal(graph, node_id) or "None"


def _first_size(graph: StrategyGraph, node_id: str) -> str | None:
    for src, port in _incoming(graph, node_id):
        if port == "size":
            return src
    return None


def generate(graph: StrategyGraph) -> str:
    """Generate the Python module text for ``graph`` (deterministic)."""
    klass = _class_name(graph.name)
    layout = _parameter_layout(graph)
    out: list[str] = []
    out.append(f'"""{klass}: generated from a Honba StrategyGraph (schema_version 1).')
    out.append("")
    out.append("DO NOT EDIT by hand: ``honba.strategies.graph_codegen`` regenerates this file")
    out.append("from the graph; see the builder block below for the lossless source.")
    out.append('"""')
    out.append("")
    out.append(MARKER)
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from honba.strategies.base import Parameter, Strategy")
    out.append("from honba.strategies.indicators import build_indicator")
    out.append("from honba.strategies import graph as _graph")
    out.append("")
    out.append(f"GRAPH_HASH = {graph.graph_hash()!r}")
    out.append("")
    out.append("GRAPH = (")
    out.append("    _graph.StrategyGraph.build(")
    out.append(f"        name={graph.name!r},")
    out.append(
        f"        universe={_lit(graph.universe.model_dump(mode='json', exclude_none=True))},"
    )
    out.append(
        f"        driving_timeframe={_lit(graph.driving_timeframe.model_dump(mode='json'))},"
    )
    out.append(f"        warmup_bars={graph.warmup_bars!r},")
    out.append("    )")
    out.append(
        f"    .with_subscriptions(quotes={graph.subscriptions.quotes!r}, "
        f"trades={graph.subscriptions.trades!r})"
    )
    for node in graph.nodes:
        pairs: list[tuple[str, Any]] = [("id", node.id), ("type", node.type)]
        if node.label:
            pairs.append(("label", node.label))
        if node.indicator:
            pairs.append(("indicator", node.indicator))
        if node.params:
            pairs.append(("params", dict(sorted(node.params.items()))))
        out.append(f"    .node({_kwargs(pairs)})")
    for edge in graph.edges:
        pairs = [
            ("id", edge.id),
            ("from_node", edge.from_node),
            ("from_port", edge.from_port),
            ("to_node", edge.to_node),
            ("to_port", edge.to_port),
        ]
        out.append(f"    .edge({_kwargs(pairs)})")
    out.append(")")
    out.append("")
    out.append("")
    out.append(CLASS_MARKER)
    out.append(f"class {klass}(Strategy):")
    out.append(f"    name = {graph.name!r}")
    out.append(f"    warmup_bars = {graph.warmup_bars!r}")
    for node in _indicator_instances(graph):
        for pname, pvalue in node.params.items():
            attr = layout[(node.id, pname)]
            out.append(f"    {attr} = Parameter(default={pvalue!r}, description={pname!r})")
    if not _indicator_instances(graph):
        out.append("    pass")
    out.append("")
    out.append("    def on_start(self) -> None:")
    started = False
    for node in _indicator_instances(graph):
        kwargs = ", ".join(f"{p}=self.{layout[(node.id, p)]}" for p in sorted(node.params))
        call = f"build_indicator({node.indicator!r}{', ' if kwargs else ''}{kwargs})"
        out.append(f"        self._{_ident(node.id)} = {call}")
        started = True
    if not started:
        out.append("        pass")
    out.append("")
    out.append("    def on_bar(self, bar) -> None:")
    body = _generate_on_bar(graph, layout)
    out.extend(body)
    out.append("")
    return "\n".join(out)
