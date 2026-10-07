"""Optional MCP protocol adapter over :class:`~honba.ai.mcp.handlers.McpGateway`.

Nothing here imports the ``mcp`` package at module import time: the package is an optional
extra (``honba[ai]``). :func:`tool_descriptors` is SDK-free and is what the adapter sends;
:func:`build_server` and :func:`run_stdio` import ``mcp`` when called and raise a clear error
when it is missing.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from honba.ai.mcp.handlers import McpError, McpGateway

__all__ = [
    "build_server",
    "call_tool_handler",
    "list_tools_handler",
    "main",
    "run_stdio",
    "tool_descriptors",
]


def tool_descriptors(gateway: McpGateway) -> list[dict[str, Any]]:
    """The gateway's tools as MCP ``Tool`` records (plain dicts, no SDK)."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": dict(spec.input_schema),
            "annotations": {"readOnlyHint": spec.read_only},
        }
        for spec in gateway.list_tools()
    ]


def _error_content(err: McpError) -> str:
    return json.dumps(
        {
            "error": {
                "code": err.code,
                "message": err.message,
                "context": err.context,
                "retryable": err.retryable,
            }
        },
        ensure_ascii=False,
    )


def _import_types() -> Any:
    try:
        from mcp import types
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError(
            "the MCP server needs the optional 'mcp' package; install honba[ai]"
        ) from exc
    return types


def list_tools_handler(gateway: McpGateway) -> Any:
    """An async handler returning the gateway's tools as an ``mcp`` ``ListToolsResult``."""
    types = _import_types()

    def _to_tool(spec: Any) -> Any:
        return types.Tool(
            name=spec.name,
            description=spec.description,
            inputSchema=dict(spec.input_schema),
            annotations=types.ToolAnnotations(read_only_hint=spec.read_only),
        )

    async def on_list_tools(_context: Any, _params: Any) -> Any:
        return types.ListToolsResult(tools=[_to_tool(spec) for spec in gateway.list_tools()])

    return on_list_tools


def call_tool_handler(gateway: McpGateway) -> Any:
    """An async handler that runs a tool and returns an ``mcp`` ``CallToolResult``."""
    types = _import_types()

    async def on_call_tool(_context: Any, params: Any) -> Any:
        try:
            result = gateway.call_tool(params.name, params.arguments or {})
        except McpError as err:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=_error_content(err))], isError=True
            )
        return types.CallToolResult(content=[types.TextContent(type="text", text=result.content)])

    return on_call_tool


def build_server(gateway: McpGateway) -> Any:
    """Build an ``mcp.server.Server`` that lists and calls the gateway's tools.

    Raises:
        RuntimeError: the ``mcp`` package is not installed.
    """
    try:
        from mcp.server import Server
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError(
            "the MCP server needs the optional 'mcp' package; install honba[ai]"
        ) from exc

    return Server(
        "honba",
        on_list_tools=list_tools_handler(gateway),
        on_call_tool=call_tool_handler(gateway),
    )


async def run_stdio(gateway: McpGateway) -> None:
    """Serve the gateway over stdio (the local default transport)."""
    from mcp.server.stdio import stdio_server

    server = build_server(gateway)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m honba.ai.mcp.server``: serve the gateway over stdio for a data directory."""
    parser = argparse.ArgumentParser(description="Honba MCP server (read-only by default)")
    parser.add_argument("--data-dir", required=True, help="Parquet catalog for the in-proc client")
    parser.add_argument("--base-url", default=None, help="use a running honba serve instead")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="turn the read-only kill switch off (writes then flow through the REST API)",
    )
    options = parser.parse_args(argv)

    import asyncio

    from honba.client import Client

    client = Client.http(options.base_url) if options.base_url else Client.inproc(options.data_dir)
    gateway = McpGateway(client, read_only=not options.allow_writes)
    try:
        asyncio.run(run_stdio(gateway))
    finally:
        client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
