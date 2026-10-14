"""The versioned :class:`StrategyGraph` model (E9-S1, ADR 0029).

A ``StrategyGraph`` is a typed, data-only description of an algorithmic strategy: a set of
typed nodes (universe/data, indicator, transform, condition, logic, sizing, risk, order,
schedule, benchmark) wired by typed edges, plus the manifest-level facts (name, universe,
driving timeframe, warm-up). It is the visual surface of the AlgoDesigner and the canonical
artifact persisted next to a generated ``.py`` file (D16).

Design rules (ADR 0029):

* **The node catalogue is derived, never hand-listed.** Indicator nodes come from the
  registered Python indicators (``honba.strategies.indicators``): kinds, families, inputs,
  outputs, parameter names, defaults, types and sweep ranges are introspected from the
  indicator classes. Adding an indicator makes it a designer node with no edit here.
* **Validation returns the same error codes as ``honba verify``.** The manifest-level
  checks (``empty_name``, ``zero_interval``, ``no_subscriptions``,
  ``subscription_outside_universe``, ...) mirror ``StrategyManifest.validate_manifest`` and
  ``StrategyIr::compile`` so a graph that fails here fails ``honba verify`` with the same
  code. Graph-only defects (a dangling edge, an unknown indicator) use graph-specific codes.
* **Canonical JSON is stable.** ``canonical_json`` sorts keys and uses compact separators,
  so ``graph_hash`` is a deterministic function of the graph's content.

The JSON shape (``schema_version`` 1) is::

    {
      "schema_version": 1,
      "name": "sma_crossover",
      "universe": {"named": "NIFTY50"} | {"explicit": [{"symbol": ..., "exchange": ...}]},
      "driving_timeframe": {"interval": 1, "aggregation": "day"},
      "warmup_bars": 20,
      "subscriptions": {"quotes": false, "trades": false},
      "nodes": [{"id", "type", "label", "params", "indicator"?}],
      "edges": [{"id", "from_node", "from_port", "to_node", "to_port"}]
    }
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from honba.entities.instrument import InstrumentId
from honba.strategies.indicators import indicator_spec, list_indicators
from honba.strategies.manifest import StrategyManifest, Subscriptions, TimeframeSpec, Universe
from honba.wire.wire import BarAggregation

__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "NODE_TYPES",
    "GraphEdge",
    "GraphError",
    "GraphNode",
    "StrategyGraph",
    "graph_hash",
    "indicator_catalogue",
    "node_catalogue",
    "node_spec",
]

GRAPH_SCHEMA_VERSION = 1
"""The StrategyGraph document version this module reads and writes."""

#: The typed node kinds of the designer. ``universe`` is the data root; ``benchmark`` is a
#: passive comparison series; the rest form the decision pipeline. Port types are declared in
#: ``_NODE_PORT_SPECS``.
NODE_TYPES: tuple[str, ...] = (
    "universe",
    "indicator",
    "transform",
    "condition",
    "logic",
    "sizing",
    "risk",
    "order",
    "schedule",
    "benchmark",
)

#: Input and output port name -> port type, per node type. A port type is one of
#: ``series``, ``bool``, ``size``, ``order``.
_NODE_PORT_SPECS: dict[str, dict[str, dict[str, str]]] = {
    "universe": {"inputs": {}, "outputs": {"bars": "series"}},
    "indicator": {"inputs": {"input": "series"}, "outputs": {"value": "series"}},
    "transform": {"inputs": {"input": "series"}, "outputs": {"value": "series"}},
    "condition": {"inputs": {"left": "series", "right": "series"}, "outputs": {"signal": "bool"}},
    "logic": {"inputs": {"a": "bool", "b": "bool"}, "outputs": {"signal": "bool"}},
    "sizing": {"inputs": {"signal": "bool"}, "outputs": {"size": "size"}},
    "risk": {"inputs": {"signal": "bool"}, "outputs": {"signal": "bool"}},
    "order": {"inputs": {"signal": "bool", "size": "size"}, "outputs": {"orders": "order"}},
    "schedule": {"inputs": {}, "outputs": {"signal": "bool"}},
    "benchmark": {"inputs": {"input": "series"}, "outputs": {"value": "series"}},
}

#: Condition operators the designer offers (a subset of the screener's cross/compare set).
CONDITION_OPS: tuple[str, ...] = (
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "crosses_above",
    "crosses_below",
)

#: Logic operators.
LOGIC_OPS: tuple[str, ...] = ("and", "or", "not")

#: Sizing modes.
SIZING_MODES: tuple[str, ...] = ("fixed_quantity", "percent_equity", "fixed_notional")


class GraphError(ValueError):
    """Why a graph was rejected.

    ``code`` matches ``honba verify``'s error codes for manifest-level defects (so the same
    code, and only graph-specific codes otherwise); ``node_id`` names the offending node when
    the defect is local to one.
    """

    def __init__(self, code: str, message: str, *, node_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.node_id = node_id


class GraphNode(BaseModel):
    """One typed node: an ``id``, a ``type`` and its parameters."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    label: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    indicator: str | None = None

    def display(self) -> str:
        return self.label or self.indicator or self.type


class GraphEdge(BaseModel):
    """A typed connection from an output port to an input port."""

    model_config = ConfigDict(extra="forbid")

    id: str
    from_node: str
    from_port: str
    to_node: str
    to_port: str


class _UniverseJSON(BaseModel):
    model_config = ConfigDict(extra="forbid")
    named: str | None = None
    explicit: list[dict[str, str]] | None = None


class _SubscriptionsJSON(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotes: bool = False
    trades: bool = False


class _TimeframeJSON(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval: int = Field(ge=0)
    aggregation: str


class StrategyGraph(BaseModel):
    """A versioned, typed strategy graph (see the module docstring for the JSON shape)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = GRAPH_SCHEMA_VERSION
    name: str
    universe: _UniverseJSON
    driving_timeframe: _TimeframeJSON
    warmup_bars: int = Field(default=0, ge=0)
    subscriptions: _SubscriptionsJSON = Field(default_factory=_SubscriptionsJSON)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    strategy_api_version: str | None = None
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # -- constructors ----------------------------------------------------------
    @classmethod
    def build(
        cls,
        name: str,
        *,
        universe: str | list[InstrumentId] | Mapping[str, Any],
        driving_timeframe: TimeframeSpec | tuple[int, str] | Mapping[str, Any],
        warmup_bars: int = 0,
        **kwargs: Any,
    ) -> StrategyGraph:
        """Build a graph, normalising ergonomic universe/timeframe arguments to JSON."""
        return cls(
            name=name,
            universe=_universe_json(universe),
            driving_timeframe=_timeframe_json(driving_timeframe),
            warmup_bars=warmup_bars,
            **kwargs,
        )

    # -- builder (used by graph->code and by hand-written graphs) ---------------
    def node(
        self,
        *,
        id: str,
        type: str,
        label: str = "",
        params: Mapping[str, Any] | None = None,
        indicator: str | None = None,
    ) -> StrategyGraph:
        """Append a node and return ``self`` (builder chaining)."""
        self.nodes.append(
            GraphNode(id=id, type=type, label=label, params=dict(params or {}), indicator=indicator)
        )
        return self

    def edge(
        self,
        *,
        id: str,
        from_node: str,
        from_port: str,
        to_node: str,
        to_port: str,
    ) -> StrategyGraph:
        """Append an edge and return ``self`` (builder chaining)."""
        self.edges.append(
            GraphEdge(
                id=id, from_node=from_node, from_port=from_port, to_node=to_node, to_port=to_port
            )
        )
        return self

    def with_subscriptions(self, *, quotes: bool = False, trades: bool = False) -> StrategyGraph:
        """Set the subscription channels and return ``self`` (builder chaining)."""
        self.subscriptions = _SubscriptionsJSON(quotes=quotes, trades=trades)
        return self

    # -- canonical form --------------------------------------------------------
    def canonical_dict(self) -> dict[str, Any]:
        """The graph as a plain, JSON-ready dict (keys sorted for stability)."""
        return _sorted(self.model_dump(mode="json", exclude_none=True))

    def canonical_json(self) -> str:
        """Canonical compact JSON text; two equal graphs hash identically."""
        return json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def graph_hash(self) -> str:
        """``sha256:`` plus the hex digest of the canonical graph JSON."""
        return graph_hash(self)

    # -- manifest bridge -------------------------------------------------------
    def instruments(self) -> list[InstrumentId]:
        """The explicit universe instruments (empty for a named universe)."""
        return [InstrumentId(i["symbol"], i["exchange"]) for i in (self.universe.explicit or [])]

    def manifest_name(self) -> str:
        return self.name

    def node_by_id(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def to_manifest(self) -> StrategyManifest:
        """The :class:`StrategyManifest` this graph declares (unresolved named universe)."""
        from honba.strategies.manifest import ParameterSpec

        aggregation = _aggregation(self.driving_timeframe.aggregation)
        if self.universe.named is not None:
            universe = Universe.of_named(self.universe.named)
            ids: list[InstrumentId] = []
        else:
            ids = self.instruments()
            universe = Universe.of_explicit(ids)
        parameters = {
            key: ParameterSpec.model_validate(value) for key, value in self.parameters.items()
        }
        return StrategyManifest(
            api_version=self.strategy_api_version or _default_api_version(),
            name=self.name,
            source_hash=self.graph_hash(),
            universe=universe,
            subscriptions=Subscriptions.of(
                ids, quotes=self.subscriptions.quotes, trades=self.subscriptions.trades
            ),
            driving_timeframe=TimeframeSpec(
                interval=self.driving_timeframe.interval, aggregation=aggregation
            ),
            warmup_bars=self.warmup_bars,
            parameters=parameters,
        )

    # -- validation ------------------------------------------------------------
    def validate(self) -> None:
        """Raise :class:`GraphError` for any defect, using ``honba verify``'s codes.

        Structural checks (typed nodes/ports/edges) run first; then the manifest-level rules
        are applied so the code matches ``honba verify`` exactly for shared defects.
        """
        # --- structural (graph-only codes) ---
        if self.schema_version != GRAPH_SCHEMA_VERSION:
            raise GraphError(
                "unsupported_schema_version",
                f"graph schema_version {self.schema_version} is not {GRAPH_SCHEMA_VERSION}",
            )
        if not self.nodes:
            raise GraphError("empty_graph", "a strategy graph must have at least one node")
        seen_ids: set[str] = set()
        for node in self.nodes:
            if node.id in seen_ids:
                raise GraphError(
                    "duplicate_node_id", f"duplicate node id {node.id!r}", node_id=node.id
                )
            seen_ids.add(node.id)
            if node.type not in NODE_TYPES:
                raise GraphError(
                    "unknown_node_type", f"unknown node type {node.type!r}", node_id=node.id
                )
            if node.type == "indicator":
                if not node.indicator:
                    raise GraphError(
                        "missing_indicator",
                        "indicator node needs an 'indicator' kind",
                        node_id=node.id,
                    )
                if node.indicator not in _Catalogue.kinds():
                    raise GraphError(
                        "unknown_indicator",
                        f"unknown indicator {node.indicator!r}",
                        node_id=node.id,
                    )
                _check_indicator_params(node)
        if not any(n.type == "universe" for n in self.nodes):
            raise GraphError("missing_universe_node", "the graph has no universe node")
        seen_edges: set[str] = set()
        for edge in self.edges:
            if edge.id in seen_edges:
                raise GraphError("duplicate_edge_id", f"duplicate edge id {edge.id!r}")
            seen_edges.add(edge.id)
            source = self.node_by_id(edge.from_node)
            target = self.node_by_id(edge.to_node)
            if source is None or target is None:
                bad = edge.from_node if source is None else edge.to_node
                raise GraphError(
                    "unknown_edge_endpoint", f"edge {edge.id!r} references unknown node {bad!r}"
                )
            out_ports = _NODE_PORT_SPECS[source.type]["outputs"]
            in_ports = _NODE_PORT_SPECS[target.type]["inputs"]
            if edge.from_port not in out_ports:
                raise GraphError(
                    "unknown_port",
                    f"node {source.id!r} has no output port {edge.from_port!r}",
                    node_id=source.id,
                )
            if edge.to_port not in in_ports:
                raise GraphError(
                    "unknown_port",
                    f"node {target.id!r} has no input port {edge.to_port!r}",
                    node_id=target.id,
                )
            if out_ports[edge.from_port] != in_ports[edge.to_port]:
                raise GraphError(
                    "port_type_mismatch",
                    f"edge {edge.id!r} connects "
                    f"{out_ports[edge.from_port]!r} to {in_ports[edge.to_port]!r}",
                )

        # --- manifest-level (honba verify codes) ---
        _check_manifest_errors(self.to_manifest())


def graph_hash(graph: StrategyGraph) -> str:
    """``sha256:`` plus the hex digest of ``graph.canonical_json()``."""
    digest = hashlib.sha256(graph.canonical_json().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Node catalogue (derived from the registered Python indicators)
# ---------------------------------------------------------------------------


def _param_range(name: str, default: Any, type_name: str) -> dict[str, Any]:
    """Derive a UI sweep range for one indicator parameter (deterministic, never hand-listed)."""
    if isinstance(default, bool) or type_name == "bool":
        return {"min": None, "max": None, "step": None}
    if isinstance(default, int) and type_name != "float":
        if default <= 0:
            return {"min": None, "max": None, "step": None}
        return {"min": 1, "max": max(default * 3, default + 1), "step": 1}
    if isinstance(default, float):
        hi = default * 3 if default > 0 else 1.0
        step = round(default / 10.0, 6) if default > 0 else 1.0
        return {"min": 0.0, "max": hi, "step": step or 1.0}
    return {"min": None, "max": None, "step": None}


class _Catalogue:
    """The designer's node catalogue, derived once from the indicator registry."""

    _indicators: dict[str, dict[str, Any]] | None = None

    @classmethod
    def kinds(cls) -> set[str]:
        return set(cls.indicators())

    @classmethod
    def indicators(cls) -> dict[str, dict[str, Any]]:
        if cls._indicators is None:
            built: dict[str, dict[str, Any]] = {}
            for kind in list_indicators():
                spec = indicator_spec(kind)
                params = []
                for p in spec["params"]:
                    entry = {
                        "name": p["name"],
                        "default": p.get("default"),
                        "type": p.get("type", "float"),
                    }
                    entry.update(_param_range(p["name"], p.get("default"), entry["type"]))
                    params.append(entry)
                built[kind] = {
                    "kind": kind,
                    "family": spec["family"],
                    "inputs": list(spec["inputs"]),
                    "outputs": list(spec["outputs"]),
                    "params": params,
                    "summary": spec["summary"],
                }
            cls._indicators = built
        return cls._indicators


def indicator_catalogue() -> dict[str, dict[str, Any]]:
    """One entry per registered indicator: kind, family, inputs, outputs, params, summary."""
    return {k: dict(v) for k, v in _Catalogue.indicators().items()}


def node_spec(node_type: str) -> dict[str, Any]:
    """The ports (and, for conditions/logic/sizing, the operator choices) of a node type."""
    if node_type not in NODE_TYPES:
        raise GraphError("unknown_node_type", f"unknown node type {node_type!r}")
    spec: dict[str, Any] = dict(_NODE_PORT_SPECS[node_type])
    if node_type == "condition":
        spec["operators"] = list(CONDITION_OPS)
    elif node_type == "logic":
        spec["operators"] = list(LOGIC_OPS)
    elif node_type == "sizing":
        spec["modes"] = list(SIZING_MODES)
    return spec


def node_catalogue() -> dict[str, Any]:
    """Every node type with its ports/operators plus the derived indicator catalogue."""
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "node_types": {t: node_spec(t) for t in NODE_TYPES},
        "indicators": indicator_catalogue(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_indicator_params(node: GraphNode) -> None:
    spec = _Catalogue.indicators()[node.indicator]  # type: ignore[index]
    names = {p["name"] for p in spec["params"]}
    unknown = sorted(set(node.params) - names)
    if unknown:
        raise GraphError(
            "unknown_node_param",
            f"indicator {node.indicator!r} has no parameter(s) {unknown}",
            node_id=node.id,
        )


def _check_manifest_errors(manifest: StrategyManifest) -> None:
    """Mirror ``StrategyManifest.validate_manifest`` + ``StrategyIr::compile`` error codes."""
    if not manifest.name.strip():
        raise GraphError("empty_name", "strategy name must not be empty")
    if not manifest.source_hash.strip():
        raise GraphError("empty_source_hash", "source_hash must not be empty")
    expected = _default_api_version()
    if manifest.api_version != expected:
        raise GraphError(
            "unsupported_api_version", f"unsupported strategy api_version: {manifest.api_version}"
        )
    if manifest.driving_timeframe.interval == 0:
        raise GraphError("zero_interval", "timeframe interval must be > 0")
    if manifest.universe.named is not None and not manifest.subscriptions.instruments:
        raise GraphError(
            "named_universe_unresolved",
            "named universe must be resolved to instruments before running",
        )
    if not manifest.subscriptions.instruments:
        raise GraphError("no_subscriptions", "strategy subscribes to no instruments")
    subscribed = {i.to_domain() for i in manifest.subscriptions.instruments}
    if manifest.universe.explicit is not None:
        declared = {i.to_domain() for i in manifest.universe.explicit}
        outside = sorted(subscribed - declared, key=lambda i: (i.symbol, i.exchange))
        if outside:
            raise GraphError(
                "subscription_outside_universe",
                f"subscription {outside[0]} is outside the strategy's universe",
            )
    for name, param in manifest.parameters.items():
        if param.min is not None and param.max is not None and param.min > param.max:
            raise GraphError("invalid_parameter_range", f"parameter {name!r} min > max")
        if param.step is not None and param.step <= 0:
            raise GraphError("invalid_parameter_range", f"parameter {name!r} step <= 0")
        if isinstance(param.default, (int, float)) and not isinstance(param.default, bool):
            if param.min is not None and param.default < param.min:
                raise GraphError(
                    "parameter_default_out_of_range", f"parameter {name!r} default < min"
                )
            if param.max is not None and param.default > param.max:
                raise GraphError(
                    "parameter_default_out_of_range", f"parameter {name!r} default > max"
                )


def _aggregation(value: str) -> BarAggregation:
    try:
        return BarAggregation(value)
    except ValueError:
        # Accept the Rust Debug spelling too ("Day" -> "day").
        try:
            return BarAggregation(value.lower())
        except ValueError as exc:
            raise GraphError("invalid_aggregation", f"unknown bar aggregation {value!r}") from exc


def _default_api_version() -> str:
    from honba._native import native_attr

    try:
        return str(native_attr("STRATEGY_API_VERSION"))
    except (ImportError, RuntimeError):
        return "1.0.0"


def _universe_json(
    universe: str | list[InstrumentId] | Mapping[str, Any],
) -> _UniverseJSON:
    if isinstance(universe, str):
        return _UniverseJSON(named=universe)
    if isinstance(universe, Mapping):
        return _UniverseJSON.model_validate(universe)
    return _UniverseJSON(explicit=[{"symbol": i.symbol, "exchange": i.exchange} for i in universe])


def _timeframe_json(
    timeframe: TimeframeSpec | tuple[int, str] | Mapping[str, Any],
) -> _TimeframeJSON:
    if isinstance(timeframe, TimeframeSpec):
        return _TimeframeJSON(
            interval=timeframe.interval, aggregation=str(timeframe.aggregation.value)
        )
    if isinstance(timeframe, tuple):
        return _TimeframeJSON(interval=int(timeframe[0]), aggregation=str(timeframe[1]))
    return _TimeframeJSON.model_validate(timeframe)


def _sorted(value: Any) -> Any:
    """Recursively sort mapping keys so canonical JSON is order-independent."""
    if isinstance(value, Mapping):
        return {k: _sorted(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted(v) for v in value]
    return value
