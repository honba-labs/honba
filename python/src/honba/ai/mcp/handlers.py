"""The MCP gateway: tool listing and tool calls over :class:`honba.client.Client`.

This is the logic of the thin MCP server, with no dependency on the optional ``mcp`` SDK
(that adapter lives in :mod:`honba.ai.mcp.server`). It exposes the generated toolset, routes
every call through the ``Client`` so the answer equals the REST response, refuses writes when
the read-only switch is on, and wraps free-text fields of a result in the trust envelope
(:mod:`honba.ai.trust`) before handing them to an agent.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from honba.ai.mcp.tools import ToolSpec, build_request, load_tools
from honba.ai.trust import wrap_untrusted
from honba.client.errors import ApiError

__all__ = ["FREE_TEXT_KEYS", "McpError", "McpGateway", "ToolResult"]

#: Result keys whose string values are free text an adapter or model wrote, not the domain.
#: Their values are wrapped in a trust envelope before an agent sees them.
FREE_TEXT_KEYS: frozenset[str] = frozenset(
    {"symbol", "name", "message", "notes", "description", "reason", "detail", "label", "text"}
)


class McpError(Exception):
    """A typed MCP failure with a stable code.

    The code is either one of the MCP codes below or the REST envelope's own code
    (``instrument_not_found``, ``validation_invalid_request``, ...) so a caller sees the same
    error whichever surface raised it.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: Any = None,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.context = context
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of one tool call.

    ``data`` is the REST envelope's ``data`` exactly as the server returned it; ``content`` is
    the same payload as JSON text with every free-text field replaced by a trust envelope.
    """

    tool: str
    data: Any
    content: str
    is_error: bool = False


class McpGateway:
    """A toolset bound to a client, with an optional read-only kill switch.

    Args:
        client: an object with ``request_data(method, path, query=..., body=...)``
            (normally :class:`honba.client.Client`).
        tools: the tools to expose; defaults to the generated ones.
        toolset: an explicit allowlist of tool names (the "selected toolset"); defaults to all.
        read_only: when true (the default), a tool whose ``readOnlyHint`` is false is refused.
        free_text_keys: result keys treated as untrusted free text.
    """

    def __init__(
        self,
        client: Any,
        *,
        tools: Iterable[ToolSpec] | None = None,
        toolset: Iterable[str] | None = None,
        read_only: bool = True,
        free_text_keys: Iterable[str] = FREE_TEXT_KEYS,
    ) -> None:
        self._client = client
        self._read_only = read_only
        self._free_text_keys = frozenset(free_text_keys)
        available = {spec.name: spec for spec in (tools if tools is not None else load_tools())}

        if toolset is None:
            selected = available
        else:
            selected = {}
            for name in toolset:
                if name not in available:
                    raise McpError(
                        "mcp_unknown_tool",
                        f"tool {name!r} is not in the toolset",
                        context={"tool": name},
                    )
                selected[name] = available[name]
        self._tools: dict[str, ToolSpec] = selected

    def list_tools(self) -> list[ToolSpec]:
        """The exposed tools, in the generated order."""
        return list(self._tools.values())

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        """Run one tool and return its result.

        Raises:
            McpError: ``mcp_unknown_tool`` for a tool that is not exposed, ``mcp_read_only``
                for a write while the read-only switch is on, or the REST envelope's code for a
                failed call.
        """
        spec = self._tools.get(name)
        if spec is None:
            raise McpError(
                "mcp_unknown_tool", f"tool {name!r} is not exposed", context={"tool": name}
            )
        if self._read_only and not spec.read_only:
            raise McpError(
                "mcp_read_only",
                f"tool {name!r} can change state and the read-only switch is on",
                context={"tool": name},
            )

        method, path, query, body = build_request(spec, arguments or {})
        try:
            data = self._client.request_data(method, path, query=query, body=body)
        except ApiError as err:
            raise McpError(
                err.code,
                err.message,
                context=err.context,
                status=err.status,
                retryable=err.retryable,
            ) from err

        wrapped = self._wrap_free_text(data, origin_prefix=f"mcp:{name}")
        return ToolResult(
            tool=name,
            data=data,
            content=json.dumps(wrapped, ensure_ascii=False, allow_nan=False),
        )

    def _wrap_free_text(self, value: Any, *, origin_prefix: str, field: str | None = None) -> Any:
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if isinstance(item, str) and key in self._free_text_keys:
                    out[key] = wrap_untrusted(item, f"{origin_prefix}:{key}").to_dict()
                else:
                    out[key] = self._wrap_free_text(
                        item, origin_prefix=origin_prefix, field=str(key)
                    )
            return out
        if isinstance(value, (list, tuple)):
            return [
                self._wrap_free_text(item, origin_prefix=origin_prefix, field=field)
                for item in value
            ]
        return value
