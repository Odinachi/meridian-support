"""MCP ``list_products`` — validation, size limits, logging, and trace correlation."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event

_LOG = logging.getLogger("meridian.tools.list_products")

_MAX_CATEGORY_LEN = 120
_MAX_RESPONSE_CHARS = 400_000
# Meridian categories are human labels; block control chars and obvious injection.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ListProductsValidationError(ValueError):
    """Inputs failed guardrails before calling MCP."""


class ListProductsMCPError(RuntimeError):
    """MCP returned an error or unreadable payload."""


def _sanitize_category(category: str | None) -> str | None:
    if category is None:
        return None
    if not isinstance(category, str):
        raise ListProductsValidationError("Category must be text.")

    s = category.strip()
    if not s:
        return None
    if len(s) > _MAX_CATEGORY_LEN:
        raise ListProductsValidationError(
            f"Category is too long (max {_MAX_CATEGORY_LEN} characters)."
        )
    if _CTRL_RE.search(s):
        raise ListProductsValidationError("Category contains characters that are not allowed.")
    return s


def _sanitize_is_active(is_active: bool | None) -> bool | None:
    if is_active is None:
        return None
    if not isinstance(is_active, bool):
        raise ListProductsValidationError("is_active must be true, false, or omitted.")
    return is_active


def _build_mcp_arguments(
    *,
    category: str | None,
    is_active: bool | None,
) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if category is not None:
        args["category"] = category
    if is_active is not None:
        args["is_active"] = is_active
    return args


def _customer_suffix(customer_id: str) -> str:
    cid = (customer_id or "").strip()
    if len(cid) <= 8:
        return cid or "?"
    return cid[-8:]


def fetch_list_products(
    *,
    acting_customer_id: str,
    category: str | None = None,
    is_active: bool | None = None,
    max_response_chars: int = _MAX_RESPONSE_CHARS,
) -> str:
    """
    Call MCP ``list_products`` with guardrails and observability.

    - Validates ``category`` and ``is_active`` before any network I/O.
    - Requires ``acting_customer_id`` (enforced again in ``mcp_guard``).
    - Logs start, outcome, duration, trace_id, and a short customer id suffix.
    - Caps response size to protect the UI and logs.
    """
    cat = _sanitize_category(category)
    act = _sanitize_is_active(is_active)
    args = _build_mcp_arguments(category=cat, is_active=act)
    suffix = _customer_suffix(acting_customer_id)
    trace = get_trace_id()

    log_tool_event(
        _LOG,
        logging.INFO,
        "list_products.request",
        tool="list_products",
        customer_id_suffix=suffix,
        extra_fields={
            "has_category": cat is not None,
            "is_active": act,
            "trace": trace,
            "arg_keys": sorted(args.keys()),
        },
    )

    t0 = time.perf_counter()
    try:
        result = call_tool_sync_guarded(
            "list_products",
            args,
            acting_customer_id=acting_customer_id,
        )
    except MCPAuthRequired:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "list_products.auth_blocked",
            tool="list_products",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace},
        )
        raise
    except Exception as exc:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "list_products.transport_error",
            tool="list_products",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=suffix,
            extra_fields={"trace": trace, "exc_type": type(exc).__name__},
        )
        _LOG.exception("list_products MCP call failed")
        raise ListProductsMCPError("Could not load products right now.") from exc

    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = tool_result_text(result)

    if result.isError or "Error executing tool" in text:
        log_tool_event(
            _LOG,
            logging.WARNING,
            "list_products.mcp_error",
            tool="list_products",
            duration_ms=elapsed_ms,
            customer_id_suffix=suffix,
            extra_fields={
                "trace": trace,
                "preview": (text[:200] + "…") if len(text) > 200 else text,
            },
        )
        raise ListProductsMCPError(
            "The catalog service returned an error. Try a narrower filter or again later."
        )

    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars] + "\n… [truncated for size]"
        truncated = True
        _LOG.warning(
            "list_products.response_truncated len>%s trace=%s",
            max_response_chars,
            trace or "-",
        )

    log_tool_event(
        _LOG,
        logging.INFO,
        "list_products.success",
        tool="list_products",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={
            "trace": trace,
            "chars": len(text),
            "truncated": truncated,
            "is_error": bool(result.isError),
        },
    )
    return text
