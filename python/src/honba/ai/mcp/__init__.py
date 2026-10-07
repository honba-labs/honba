"""Thin MCP server over the generated tool schemas.

The tool list and its input schemas come from ``schema/mcp/mcp_tools.json`` (generated from
the Rust endpoint registry); :class:`McpGateway` routes every call through
:class:`honba.client.Client`, refuses writes while the read-only switch is on, and wraps
free-text result fields in a trust envelope. The ``mcp`` SDK adapter in
:mod:`honba.ai.mcp.server` is imported lazily: the package is an optional extra.
"""

from honba.ai.mcp.handlers import McpError, McpGateway, ToolResult
from honba.ai.mcp.tools import ToolSpec, load_tools

__all__ = ["McpError", "McpGateway", "ToolResult", "ToolSpec", "load_tools"]
