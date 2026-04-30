"""MCP ``get_order`` — UUID validation, ownership check on response, logging, traces."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event
from meridian.tools.get_customer import normalize_customer_uuid

_LOG = logging.getLogger("meridian.tools.get_order")

_MAX_RESPONSE_CHARS = 200_000
_ORDER_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Response from Meridian MCP includes a "Customer ID:" line we use for authorization.
_ORDER_OWNER_RE = re.compile(
    r"Customer\s*ID:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


class GetOrderValidationError(ValueError):
    """``order_id`` failed format checks before calling MCP."""


class GetOrderMCPError(RuntimeError):
    """MCP returned an error or unreadable payload."""


class GetOrderNotFoundError(GetOrderMCPError):
    """No order exists for the given id."""


class GetOrderAccessError(GetOrderMCPError):
    """Order exists but is not owned by the signed-in customer (or ownership could not be verified)."""


def sanitize_order_id(order_id: str) -> str:
    if not isinstance(order_id, str):
        raise GetOrderValidationError("Order id must be a string.")
    oid = normalize_customer_uuid(order_id)
    if not oid or not _ORDER_ID_RE.match(oid):
        raise GetOrderValidationError("Order id must be a valid UUID.")
    return oid


def extract_order_owner_customer_id(response_text: str) -> str | None:
    m = _ORDER_OWNER_RE.search(response_text or "")
    return m.group(1).lower() if m else None


def assert_order_owned_by_actor(response_text: str, acting_customer_id: str) -> None:
    owner = extract_order_owner_customer_id(response_text)
    actor = normalize_customer_uuid(acting_customer_id)
    if owner is None:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_order.ownership_unverified",
            tool="get_order",
            extra_fields={"trace": get_trace_id()},
        )
        raise GetOrderAccessError(
            "We could not confirm this order belongs to your account from the service response."
        )
    if owner != actor:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_order.cross_tenant_blocked",
            tool="get_order",
            extra_fields={"trace": get_trace_id()},
        )
        raise GetOrderAccessError("That order is tied to a different Meridian account.")


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def fetch_get_order(
    *,
    acting_customer_id: str,
    order_id: str,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Fetch one order by id and ensure the payload's **Customer ID** matches ``acting_customer_id``.

    This blocks using a leaked order UUID to read another customer's basket.
    """
    oid = sanitize_order_id(order_id)
    args: dict[str, Any] = {"order_id": oid}
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "get_order.request",
        tool="get_order",
        customer_id_suffix=suffix,
        extra_fields={"trace": trace},
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "get_order",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_order.auth_blocked",
            tool="get_order",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "get_order.transport_error",
            tool="get_order",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "exc_type": type(exc).__name__},
        )
        _LOG.exception("get_order MCP call failed")
        raise GetOrderMCPError("Could not load order details right now.") from exc

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
            or "ordernotfound" in tl.replace(" ", "")
        )
        if looks_missing:
            log_tool_event(
                _LOG,
                logging.INFO,
                "get_order.not_found",
                tool="get_order",
                duration_ms=elapsed_ms,
                customer_id_suffix=suffix,
                extra_fields={"trace": trace, "preview": preview},
            )
            raise GetOrderNotFoundError("No order found for that id.")
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_order.mcp_error",
            tool="get_order",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "preview": preview},
        )
        raise GetOrderMCPError("The orders service returned an error for that id.")

    try:
        assert_order_owned_by_actor(text, acting_customer_id)
    except GetOrderAccessError:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_order.aborted_after_fetch",
            tool="get_order",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "get_order.response_truncated len>%s trace=%s",
            max_response_chars,
            trace or "-",
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "get_order.success",
        tool="get_order",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={"trace": trace, "chars": len(text), "truncated": truncated},
    )
    return text
