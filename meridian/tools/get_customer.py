"""MCP ``get_customer`` — UUID validation, self-only access, logging, and trace correlation."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event

_LOG = logging.getLogger("meridian.tools.get_customer")

_MAX_RESPONSE_CHARS = 100_000
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class GetCustomerValidationError(ValueError):
    """Customer id failed format or policy checks before MCP."""


class GetCustomerAccessError(GetCustomerValidationError):
    """Caller tried to read a customer id other than the signed-in session."""


class GetCustomerMCPError(RuntimeError):
    """MCP returned an error or unreadable payload."""


class GetCustomerNotFoundError(GetCustomerMCPError):
    """No customer exists for the given id."""


def normalize_customer_uuid(value: str) -> str:
    return value.strip().lower()


def resolve_target_customer_id(acting_customer_id: str, requested_customer_id: str | None) -> str:
    """
    Decide which UUID is sent to MCP.

    Only the **signed-in** customer's id is allowed (``requested`` must match ``acting``
    when provided). If ``requested_customer_id`` is omitted, the session id is used.
    """
    act = normalize_customer_uuid(acting_customer_id)
    if not act or not _UUID_RE.match(act):
        raise GetCustomerValidationError("Session customer id is not a valid UUID.")

    if requested_customer_id is None or not str(requested_customer_id).strip():
        return act

    req = normalize_customer_uuid(str(requested_customer_id))
    if not _UUID_RE.match(req):
        raise GetCustomerValidationError("Customer id must be a valid UUID.")
    if req != act:
        raise GetCustomerAccessError(
            "You can only load the profile for the account you're signed in with."
        )
    return act


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def fetch_get_customer(
    *,
    acting_customer_id: str,
    customer_id: str | None = None,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Call MCP ``get_customer`` for the signed-in customer only.

    ``customer_id`` is optional; when set it must equal ``acting_customer_id`` (normalized).
    """
    target = resolve_target_customer_id(acting_customer_id, customer_id)
    args: dict[str, Any] = {"customer_id": target}
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "get_customer.request",
        tool="get_customer",
        customer_id_suffix=suffix,
        extra_fields={"trace": trace, "self_lookup": True},
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "get_customer",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_customer.auth_blocked",
            tool="get_customer",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "get_customer.transport_error",
            tool="get_customer",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "exc_type": type(exc).__name__},
        )
        _LOG.exception("get_customer MCP call failed")
        raise GetCustomerMCPError("Could not load customer details right now.") from exc

    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = tool_result_text(result)
    is_err = bool(result.isError) or "Error executing tool" in text

    if is_err:
        preview = (text[:200] + "…") if len(text) > 200 else text
        tl = text.lower()
        looks_missing = (
            "not found" in tl
            or "doesn't exist" in tl
            or "does not exist" in tl
            or "customernotfound" in tl.replace(" ", "")
        )
        if looks_missing:
            log_tool_event(
                _LOG,
                logging.INFO,
                "get_customer.not_found",
                tool="get_customer",
                duration_ms=elapsed_ms,
                customer_id_suffix=suffix,
                extra_fields={"trace": trace, "preview": preview},
            )
            raise GetCustomerNotFoundError("No customer record found for this account.")
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_customer.mcp_error",
            tool="get_customer",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "preview": preview},
        )
        raise GetCustomerMCPError("The customer service returned an error.")

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "get_customer.response_truncated len>%s trace=%s",
            max_response_chars,
            trace or "-",
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "get_customer.success",
        tool="get_customer",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={"trace": trace, "chars": len(text), "truncated": truncated},
    )
    return text
