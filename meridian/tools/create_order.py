"""MCP ``create_order`` — item guardrails, self-only ``customer_id``, logging, traces."""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.models import OrderLineItem
from meridian.observability import get_trace_id, log_tool_event
from meridian.tools.get_customer import resolve_target_customer_id

_LOG = logging.getLogger("meridian.tools.create_order")

_MAX_ITEMS = 20
_MAX_RESPONSE_CHARS = 300_000

_order_lines_adapter = TypeAdapter(
    Annotated[list[OrderLineItem], Field(min_length=1, max_length=_MAX_ITEMS)]
)


class CreateOrderValidationError(ValueError):
    """Payload failed checks before calling MCP."""


class CreateOrderMCPError(RuntimeError):
    """MCP returned an error."""


class CreateOrderProductNotFoundError(CreateOrderMCPError):
    """Referenced SKU does not exist."""


class CreateOrderInsufficientInventoryError(CreateOrderMCPError):
    """Requested quantity exceeds stock."""


class CreateOrderCustomerNotFoundError(CreateOrderMCPError):
    """Customer id rejected by the service."""


def parse_order_submit_payload(text: str) -> list[dict[str, Any]] | None:
    """
    Optional demo hook: a single line ``ORDER_SUBMIT: [...]`` with a JSON array of line items.

    Example::

        ORDER_SUBMIT: [{"sku":"MON-0080","quantity":1,"unit_price":"1003.37","currency":"USD"}]
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("ORDER_SUBMIT"):
            continue
        if ":" not in stripped:
            continue
        blob = stripped.split(":", 1)[1].strip()
        if not blob.startswith("["):
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None
        if isinstance(data, list):
            return data
    return None


def _validation_error_to_create_order(exc: ValidationError) -> CreateOrderValidationError:
    errs = exc.errors()
    if not errs:
        return CreateOrderValidationError("Invalid order items.")
    e0 = errs[0]
    loc = e0.get("loc", ())
    msg = str(e0.get("msg", "invalid")).strip()
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, ") :]
    if len(loc) >= 2 and isinstance(loc[0], int):
        line_no = loc[0] + 1
        field = str(loc[1])
        return CreateOrderValidationError(f"Line {line_no} {field}: {msg}")
    if len(loc) >= 1 and isinstance(loc[0], int):
        return CreateOrderValidationError(f"Line {loc[0] + 1}: {msg}")
    if loc == ("items",) or (len(loc) == 1 and loc[0] in ("", "items")):
        return CreateOrderValidationError(msg)
    return CreateOrderValidationError(msg)


def sanitize_order_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise CreateOrderValidationError("items must be a JSON array.")
    try:
        items = _order_lines_adapter.validate_python(raw)
    except ValidationError as exc:
        raise _validation_error_to_create_order(exc) from exc
    return [line.model_dump() for line in items]


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def _classify_mcp_failure(text: str) -> CreateOrderMCPError:
    tl = text.lower()
    if "insufficient" in tl and "inventory" in tl:
        return CreateOrderInsufficientInventoryError(
            "Not enough stock for one of the items. Reduce quantity or pick another SKU."
        )
    if "product" in tl and "not found" in tl:
        return CreateOrderProductNotFoundError("One of the SKUs was not found in the catalog.")
    if "customer" in tl and "not found" in tl:
        return CreateOrderCustomerNotFoundError("Customer record was not found for this session.")
    return CreateOrderMCPError("The order service rejected the request.")


def fetch_create_order(
    *,
    acting_customer_id: str,
    items: list[dict[str, Any]],
    customer_id: str | None = None,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Place an order for the signed-in customer only.

    ``customer_id`` may only match ``acting_customer_id`` when provided.
    """
    normalized_items = sanitize_order_items(items)
    target_customer = resolve_target_customer_id(acting_customer_id, customer_id)
    args: dict[str, Any] = {
        "customer_id": target_customer,
        "items": normalized_items,
    }
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "create_order.request",
        tool="create_order",
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "line_count": len(normalized_items),
        },
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "create_order",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "create_order.auth_blocked",
            tool="create_order",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "create_order.transport_error",
            tool="create_order",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "exc_type": type(exc).__name__},
        )
        _LOG.exception("create_order MCP call failed")
        raise CreateOrderMCPError("Could not place the order right now.") from exc

    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = tool_result_text(result)
    is_err = bool(result.isError) or "Error executing tool" in text

    if is_err:
        preview = (text[:300] + "…") if len(text) > 300 else text
        log_tool_event(
            _LOG,
            logging.WARNING,
            "create_order.mcp_error",
            tool="create_order",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "preview": preview},
        )
        raise _classify_mcp_failure(text)

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "create_order.response_truncated len>%s trace=%s",
            max_response_chars,
            trace or "-",
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "create_order.success",
        tool="create_order",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "chars": len(text),
            "truncated": truncated,
            "line_count": len(normalized_items),
        },
    )
    return text
