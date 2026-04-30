"""MCP ``get_product`` — SKU validation, logging, trace correlation, and response limits."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event

_LOG = logging.getLogger("meridian.tools.get_product")

_MAX_SKU_LEN = 32
_MAX_RESPONSE_CHARS = 200_000
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Meridian SKUs observed in catalog: MON-0080, COM-0005, ACC-0162
_SKU_RE = re.compile(r"^[A-Z]{3}-\d{4}$")


class GetProductValidationError(ValueError):
    """SKU failed guardrails before calling MCP."""


class GetProductMCPError(RuntimeError):
    """MCP returned an error or unreadable payload."""


class GetProductNotFoundError(GetProductMCPError):
    """No product exists for the given SKU."""


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def sanitize_sku(sku: str) -> str:
    """
    Normalize and validate SKU (e.g. ``COM-0001``).

    Accepts any casing; MCP is called with uppercase canonical form.
    """
    if not isinstance(sku, str):
        raise GetProductValidationError("SKU must be text.")
    raw = sku.strip()
    if not raw:
        raise GetProductValidationError("SKU is required.")
    if len(raw) > _MAX_SKU_LEN:
        raise GetProductValidationError("SKU is too long.")
    if _CTRL_RE.search(raw):
        raise GetProductValidationError("SKU contains characters that are not allowed.")
    upper = raw.upper()
    if not _SKU_RE.match(upper):
        raise GetProductValidationError(
            "SKU should look like COM-0001 (three letters, a hyphen, four digits)."
        )
    return upper


def fetch_get_product(
    *,
    acting_customer_id: str,
    sku: str,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Call MCP ``get_product`` with guardrails and observability.

    Requires a signed-in customer session (see ``mcp_guard``).
    """
    canonical = sanitize_sku(sku)
    args: dict[str, Any] = {"sku": canonical}
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "get_product.request",
        tool="get_product",
        customer_id_suffix=suffix,
        extra_fields={"trace": trace, "sku": canonical},
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "get_product",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_product.auth_blocked",
            tool="get_product",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "sku": canonical},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "get_product.transport_error",
            tool="get_product",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "sku": canonical, "exc_type": type(exc).__name__},
        )
        _LOG.exception("get_product MCP call failed")
        raise GetProductMCPError("Could not load product details right now.") from exc

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
            or "productnotfound" in tl.replace(" ", "")
        )
        if looks_missing:
            log_tool_event(
                _LOG,
                logging.INFO,
                "get_product.not_found",
                tool="get_product",
                duration_ms=elapsed_ms,
                customer_id_suffix=suffix,
                extra_fields={"trace": trace, "sku": canonical, "preview": preview},
            )
            raise GetProductNotFoundError(f"No product found for SKU {canonical}.")
        log_tool_event(
            _LOG,
            logging.WARNING,
            "get_product.mcp_error",
            tool="get_product",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "sku": canonical, "preview": preview},
        )
        raise GetProductMCPError("The catalog service returned an error for that SKU.")

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "get_product.response_truncated len>%s trace=%s sku=%s",
            max_response_chars,
            trace or "-",
            canonical,
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "get_product.success",
        tool="get_product",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "sku": canonical,
            "chars": len(text),
            "truncated": truncated,
        },
    )
    return text
