"""MCP ``search_products`` — query validation, logging, trace correlation, response limits."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event

_LOG = logging.getLogger("meridian.tools.search_products")

_MAX_QUERY_LEN = 200
_MAX_RESPONSE_CHARS = 400_000
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SearchProductsValidationError(ValueError):
    """Query failed guardrails before calling MCP."""


class SearchProductsMCPError(RuntimeError):
    """MCP returned an error or unreadable payload."""


def sanitize_query(query: str) -> str:
    """Normalize search text for MCP (case-insensitive match is server-side)."""
    if not isinstance(query, str):
        raise SearchProductsValidationError("Search text must be a string.")
    q = " ".join(query.split()).strip()
    if not q:
        raise SearchProductsValidationError("Search text cannot be empty.")
    if len(q) > _MAX_QUERY_LEN:
        raise SearchProductsValidationError(
            f"Search text is too long (max {_MAX_QUERY_LEN} characters)."
        )
    if _CTRL_RE.search(q):
        raise SearchProductsValidationError("Search text contains characters that are not allowed.")
    return q


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def fetch_search_products(
    *,
    acting_customer_id: str,
    query: str,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Call MCP ``search_products`` with guardrails and observability.

    Requires a signed-in customer session (see ``mcp_guard``).
    """
    q = sanitize_query(query)
    args: dict[str, Any] = {"query": q}
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "search_products.request",
        tool="search_products",
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "query_len": len(q),
        },
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "search_products",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "search_products.auth_blocked",
            tool="search_products",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "search_products.transport_error",
            tool="search_products",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "exc_type": type(exc).__name__},
        )
        _LOG.exception("search_products MCP call failed")
        raise SearchProductsMCPError("Could not search the catalog right now.") from exc

    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = tool_result_text(result)
    is_err = bool(result.isError) or "Error executing tool" in text

    if is_err:
        preview = (text[:200] + "…") if len(text) > 200 else text
        log_tool_event(
            _LOG,
            logging.WARNING,
            "search_products.mcp_error",
            tool="search_products",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "preview": preview},
        )
        raise SearchProductsMCPError("The catalog search returned an error.")

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "search_products.response_truncated len>%s trace=%s",
            max_response_chars,
            trace or "-",
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "search_products.success",
        tool="search_products",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "chars": len(text),
            "truncated": truncated,
        },
    )
    return text
