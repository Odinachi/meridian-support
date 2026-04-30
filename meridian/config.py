import os


def _strip_optional_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1].strip()
    return v


def get_mcp_url() -> str:
    """Remote MCP endpoint (Streamable HTTP)."""
    url = os.environ.get("MCP_SERVER_URL") or os.environ.get("MCP_URL")
    if not url or not url.strip():
        raise RuntimeError(
            "Set MCP_SERVER_URL (or MCP_URL) to your Streamable HTTP MCP endpoint, "
            "e.g. https://order-mcp-….run.app/mcp"
        )
    return _strip_optional_quotes(url).rstrip("/")
