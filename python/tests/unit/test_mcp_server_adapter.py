"""Optional smoke test for the ``mcp`` SDK adapter; skipped when the extra is absent."""

from __future__ import annotations

import json
from typing import Any

import pytest

from honba.ai.mcp import server as mcp_server
from honba.ai.mcp.handlers import McpGateway

pytest.importorskip("mcp")

from mcp import types


class StubClient:
    def request_data(self, method, path, *, query=None, body=None):
        return {"instruments": []}


async def test_adapter_lists_the_selected_toolset() -> None:
    gateway = McpGateway(StubClient(), toolset={"get_instruments"})

    listed = await mcp_server.list_tools_handler(gateway)(None, None)

    assert [tool.name for tool in listed.tools] == ["get_instruments"]
    assert listed.tools[0].annotations.read_only_hint is True


async def test_adapter_calls_a_tool_and_returns_text_content() -> None:
    gateway = McpGateway(StubClient())

    result = await mcp_server.call_tool_handler(gateway)(
        None, types.CallToolRequestParams(name="get_instruments", arguments={})
    )

    assert result.is_error in (False, None)
    assert json.loads(result.content[0].text) == {"instruments": []}


async def test_adapter_reports_a_typed_error() -> None:
    gateway = McpGateway(StubClient())

    result: Any = await mcp_server.call_tool_handler(gateway)(
        None, types.CallToolRequestParams(name="no_such_tool", arguments={})
    )

    assert result.is_error is True
    assert json.loads(result.content[0].text)["error"]["code"] == "mcp_unknown_tool"
