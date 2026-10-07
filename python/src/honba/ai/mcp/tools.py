"""MCP tool loaders and request builders.

The tool list, its descriptions and its ``inputSchema`` are **generated** from the Rust
endpoint registry (``schema/mcp/mcp_tools.json``) and are never invented here. This module
loads that artifact and attaches the one thing it does not carry: the endpoint
``(method, path)`` each tool drives, kept in :data:`TOOL_BINDINGS` and asserted complete
against the artifact, so a newly generated tool fails loudly until it is wired.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

__all__ = ["ToolSpec", "build_request", "load_tools", "schema_path"]

_PATH_PARAM = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One generated MCP tool, plus the endpoint it drives."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    read_only: bool
    endpoint: tuple[str, str]


@dataclass(frozen=True, slots=True)
class _Binding:
    """How a tool's arguments become a request.

    ``body_arg`` selects the single argument sent as the JSON body; ``None`` means the whole
    argument object is the body (for a POST) or that the request carries no body (for a GET).
    """

    method: str
    path: str
    body_arg: str | None = None


#: Tool name -> the endpoint it drives. Derived from ``honba-codegen``'s ``TOOLS`` table.
TOOL_BINDINGS: dict[str, _Binding] = {
    "backtest": _Binding("POST", "/backtests"),
    "sweep": _Binding("POST", "/sweeps"),
    "verify_strategy": _Binding("POST", "/strategies/verify", body_arg="strategy_manifest"),
    "compile_strategy": _Binding("POST", "/strategies", body_arg="request"),
    "list_strategies": _Binding("GET", "/strategies"),
    "screen": _Binding("GET", "/screener/scan"),
    "get_instruments": _Binding("GET", "/instruments"),
    "get_bars": _Binding("GET", "/bars/{id}"),
}


def schema_path() -> Path:
    """The committed generated tool schemas.

    ``python/src/honba/ai/mcp/tools.py`` -> repository root -> ``schema/mcp/mcp_tools.json``.
    """
    return Path(__file__).resolve().parents[5] / "schema" / "mcp" / "mcp_tools.json"


def load_tools(path: str | Path | None = None) -> list[ToolSpec]:
    """Load the generated tools and attach their endpoint bindings.

    Raises:
        KeyError: a generated tool has no entry in :data:`TOOL_BINDINGS` (the artifact and the
            wiring drifted); wire the tool before shipping it.
    """
    source = Path(path) if path is not None else schema_path()
    document = json.loads(source.read_text(encoding="utf-8"))
    tools: list[ToolSpec] = []
    for raw in document["tools"]:
        name = raw["name"]
        binding = TOOL_BINDINGS.get(name)
        if binding is None:
            raise KeyError(
                f"tool {name!r} is generated in {source} but has no endpoint binding; "
                "add it to TOOL_BINDINGS"
            )
        tools.append(
            ToolSpec(
                name=name,
                description=raw.get("description", ""),
                input_schema=raw["inputSchema"],
                read_only=bool(raw.get("annotations", {}).get("readOnlyHint", False)),
                endpoint=(binding.method, binding.path),
            )
        )
    return tools


def build_request(
    spec: ToolSpec,
    arguments: Mapping[str, Any],
    bindings: Mapping[str, _Binding] | None = None,
) -> tuple[str, str, dict[str, Any] | None, Any]:
    """Turn a tool call into ``(method, path, query, body)``.

    Path placeholders are filled from the arguments and percent-encoded; for a ``GET`` every
    argument other than the path placeholders and the ``query`` object is sent as a query
    parameter (and the ``query`` object is flattened into it); for a ``POST`` the
    ``body_arg`` (or the whole argument object) is the body, sent verbatim.
    """
    table = bindings if bindings is not None else TOOL_BINDINGS
    binding = table.get(spec.name)
    if binding is None:
        raise KeyError(f"no endpoint binding for tool {spec.name!r}")

    path = binding.path
    path_params = set(_PATH_PARAM.findall(path))
    for param in path_params:
        if param not in arguments:
            from honba.ai.mcp.handlers import McpError

            raise McpError(
                "mcp_invalid_arguments",
                f"tool {spec.name!r} requires argument {param!r}",
                context={"tool": spec.name, "field": param},
            )
        value = str(arguments[param])
        path = path.replace("{" + param + "}", quote(value, safe="."))

    if binding.method == "GET":
        query: dict[str, Any] = {}
        raw_query = arguments.get("query")
        if isinstance(raw_query, Mapping):
            query.update(raw_query)
        for key, value in arguments.items():
            if key == "query" or key in path_params:
                continue
            query[key] = value
        return binding.method, path, (query or None), None

    if binding.body_arg is not None:
        body = arguments.get(binding.body_arg)
    else:
        body = {k: v for k, v in arguments.items() if k not in path_params}
    return binding.method, path, None, body
