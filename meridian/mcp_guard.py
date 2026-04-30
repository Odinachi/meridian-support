"""Central gate: every MCP tool except `verify_customer_pin` requires a signed-in customer."""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult

from meridian.mcp_client import call_tool_sync

_TOOLS_WITHOUT_CUSTOMER = frozenset({"verify_customer_pin"})


class MCPAuthRequired(RuntimeError):
    """Raised when an MCP tool is invoked before the user has authenticated."""


def call_tool_sync_guarded(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    acting_customer_id: str | None,
) -> CallToolResult:
    """
    Invoke an MCP tool, enforcing that a customer session exists for all tools
    except `verify_customer_pin`.
    """
    if tool_name not in _TOOLS_WITHOUT_CUSTOMER and not (acting_customer_id or "").strip():
        raise MCPAuthRequired(
            "You must complete email and account PIN verification before "
            "this assistant can access Meridian orders, products, or inventory."
        )
    return call_tool_sync(tool_name, arguments)
