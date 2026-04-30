"""MCP ``list_orders`` — self-only customer filter, status guardrails, logging, traces."""

from __future__ import annotations

import logging
import time
from typing import Any

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event
from meridian.tools.get_customer import (
    GetCustomerAccessError,
    GetCustomerValidationError,
    resolve_target_customer_id,
)

_LOG = logging.getLogger("meridian.tools.list_orders")

_MAX_RESPONSE_CHARS = 400_000
_ALLOWED_STATUS = frozenset(
    {"draft", "submitted", "approved", "fulfilled", "cancelled"}
)


class ListOrdersValidationError(ValueError):
    """Inputs failed guardrails before calling MCP."""


class ListOrdersMCPError(RuntimeError):
    """MCP returned an error or unreadable payload."""


def sanitize_status(status: str | None) -> str | None:
    if status is None:
        return None
    if not isinstance(status, str):
        raise ListOrdersValidationError("status must be a string or omitted.")
    s = status.strip().lower()
    if not s:
        return None
    if s not in _ALLOWED_STATUS:
        raise ListOrdersValidationError(
            "status must be one of: draft, submitted, approved, fulfilled, cancelled."
        )
    return s


def _build_arguments(
    *,
    acting_customer_id: str,
    customer_id: str | None,
    status: str | None,
) -> dict[str, Any]:
    """
    Always scope orders to the signed-in customer.

    ``customer_id`` may only match ``acting_customer_id`` (see ``resolve_target_customer_id``).
    """
    target = resolve_target_customer_id(acting_customer_id, customer_id)
    args: dict[str, Any] = {"customer_id": target}
    st = sanitize_status(status)
    if st is not None:
        args["status"] = st
    return args


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def fetch_list_orders(
    *,
    acting_customer_id: str,
    customer_id: str | None = None,
    status: str | None = None,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Call MCP ``list_orders`` for the signed-in customer's orders only.

    ``customer_id`` is optional; when set it must equal the session id. ``status`` is optional.
    """
    args = _build_arguments(
        acting_customer_id=acting_customer_id,
        customer_id=customer_id,
        status=status,
    )
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "list_orders.request",
        tool="list_orders",
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "has_status_filter": "status" in args,
        },
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "list_orders",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "list_orders.auth_blocked",
            tool="list_orders",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "list_orders.transport_error",
            tool="list_orders",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "exc_type": type(exc).__name__},
        )
        _LOG.exception("list_orders MCP call failed")
        raise ListOrdersMCPError("Could not load orders right now.") from exc

    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = tool_result_text(result)
    is_err = bool(result.isError) or "Error executing tool" in text

    if is_err:
        preview = (text[:200] + "…") if len(text) > 200 else text
        log_tool_event(
            _LOG,
            logging.WARNING,
            "list_orders.mcp_error",
            tool="list_orders",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "preview": preview},
        )
        raise ListOrdersMCPError("The orders service returned an error.")

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "list_orders.response_truncated len>%s trace=%s",
            max_response_chars,
            trace or "-",
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "list_orders.success",
        tool="list_orders",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "chars": len(text),
            "truncated": truncated,
        },
    )
    return text
