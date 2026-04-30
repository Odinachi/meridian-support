"""Thin MCP client — one session per tool call (stateless, safe for Streamlit reruns)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from meridian.config import get_mcp_url


@asynccontextmanager
async def mcp_session():
    url = get_mcp_url()
    async with streamable_http_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    async with mcp_session() as session:
        return await session.call_tool(name, arguments)


def call_tool_sync(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Run an MCP tool from synchronous code (e.g. Streamlit callbacks)."""
    return asyncio.run(call_tool(name, arguments))


def tool_result_text(result: CallToolResult) -> str:
    chunks: list[str] = []
    for block in result.content:
        if hasattr(block, "text") and block.text:
            chunks.append(block.text)
    return "\n".join(chunks).strip()
