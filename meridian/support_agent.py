"""OpenAI Agents SDK — post–sign-in support with gated MCP tool wrappers."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from typing import Any

from agents.agent import Agent
from agents.run import Runner
from agents.run_context import RunContextWrapper
from agents.tool import function_tool

from meridian.agent_streaming import stream_agent_text_chunks
from meridian.auth_agent import require_openai_key
from meridian.mcp_guard import MCPAuthRequired
from meridian.models import SupportAgentContext, SupportAgentResponse
from meridian.observability import get_trace_id, log_tool_event
from meridian.tools.create_order import (
    CreateOrderCustomerNotFoundError,
    CreateOrderInsufficientInventoryError,
    CreateOrderMCPError,
    CreateOrderProductNotFoundError,
    CreateOrderValidationError,
    fetch_create_order,
)
from meridian.tools.get_customer import (
    GetCustomerAccessError,
    GetCustomerMCPError,
    GetCustomerNotFoundError,
    GetCustomerValidationError,
    fetch_get_customer,
)
from meridian.tools.get_order import (
    GetOrderAccessError,
    GetOrderMCPError,
    GetOrderNotFoundError,
    GetOrderValidationError,
    fetch_get_order,
)
from meridian.tools.get_product import (
    GetProductMCPError,
    GetProductNotFoundError,
    GetProductValidationError,
    fetch_get_product,
)
from meridian.tools.list_orders import (
    ListOrdersMCPError,
    ListOrdersValidationError,
    fetch_list_orders,
)
from meridian.tools.list_products import (
    ListProductsMCPError,
    ListProductsValidationError,
    fetch_list_products,
)
from meridian.tools.search_products import (
    SearchProductsMCPError,
    SearchProductsValidationError,
    fetch_search_products,
)

_LOG = logging.getLogger("meridian.support_agent")


def _order_submit_enabled() -> bool:
    v = (os.environ.get("MERIDIAN_ENABLE_ORDER_SUBMIT") or "").strip().lower()
    return v in ("1", "true", "yes")


def _cid(ctx: RunContextWrapper[SupportAgentContext]) -> str:
    return ctx.context.acting_customer_id


def _tool_fail(name: str, exc: BaseException) -> str:
    return f"[{name}] {exc}"


SUPPORT_AGENT_INSTRUCTIONS = """You are Meridian Electronics **signed-in support**. The customer is already verified.

Tone: calm, direct, helpful—like a knowledgeable parts desk. No emoji piles, no “I’d be happy to.”

You have tools backed by the live order/catalog service. **Call tools** when the user needs facts (profile, orders, a product, search, placing an order). Do not invent SKUs, prices, stock, or order contents.

**Catalog & products**
- `meridian_get_customer` — account/profile for the signed-in customer (optional `customer_id` only if it matches their session; usually omit).
- `meridian_list_products` — catalog snapshot; optional `category` and `is_active` filter.
- `meridian_get_product` — one SKU (format like MON-0080: three letters, hyphen, four digits).
- `meridian_search_products` — text search over the catalog.

**Orders**
- `meridian_list_orders` — this customer’s orders; optional `status` (draft, submitted, approved, fulfilled, cancelled) and optional `customer_id` (must match session if set).
- `meridian_get_order` — full detail for one order UUID they provide or you already have.

**Placing orders** (`meridian_create_order`, only if this tool appears in your tool list)
- If you do not have that tool, say order placement is not enabled in this deployment and suggest they contact support or use another channel.
- Never place an order from vague chat. Confirm **SKU(s), quantities, unit prices, and currency** with the user first, then call with `items_json` set to a JSON array of objects: `sku`, `quantity`, `unit_price` (string decimal), optional `currency` (default USD).
- At most 20 lines; quantities 1–9999.

If a tool returns a message starting with `[tool_name]`, that is an error summary—acknowledge briefly and suggest a fix or next step.

Keep replies concise unless the user asked for a dump; you can summarize tool output.

**Final output (required structured object, not plain prose)**
- `reply_markdown`: your full answer for the customer (markdown).
- `tools_were_used`: true if you invoked any `meridian_*` tool on this turn to answer them; false if you answered from conversation alone.
"""


@function_tool(
    description_override="Loads the signed-in customer’s account/profile from Meridian.",
)
def meridian_get_customer(
    ctx: RunContextWrapper[SupportAgentContext],
    customer_id: str | None = None,
) -> str:
    """MCP get_customer — self only."""
    try:
        return fetch_get_customer(acting_customer_id=_cid(ctx), customer_id=customer_id)
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_get_customer", exc)
    except GetCustomerValidationError as exc:
        return _tool_fail("meridian_get_customer", exc)
    except GetCustomerAccessError as exc:
        return _tool_fail("meridian_get_customer", exc)
    except GetCustomerNotFoundError as exc:
        return _tool_fail("meridian_get_customer", exc)
    except GetCustomerMCPError as exc:
        return _tool_fail("meridian_get_customer", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_get_customer", exc)


@function_tool(
    description_override="Lists catalog products; optional category and is_active filter.",
)
def meridian_list_products(
    ctx: RunContextWrapper[SupportAgentContext],
    category: str | None = None,
    is_active: bool | None = None,
) -> str:
    """MCP list_products."""
    try:
        return fetch_list_products(
            acting_customer_id=_cid(ctx),
            category=category,
            is_active=is_active,
        )
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_list_products", exc)
    except ListProductsValidationError as exc:
        return _tool_fail("meridian_list_products", exc)
    except ListProductsMCPError as exc:
        return _tool_fail("meridian_list_products", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_list_products", exc)


@function_tool(
    description_override="Fetches one product by SKU (e.g. MON-0080).",
)
def meridian_get_product(ctx: RunContextWrapper[SupportAgentContext], sku: str) -> str:
    """MCP get_product."""
    try:
        return fetch_get_product(acting_customer_id=_cid(ctx), sku=sku)
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_get_product", exc)
    except GetProductValidationError as exc:
        return _tool_fail("meridian_get_product", exc)
    except GetProductNotFoundError as exc:
        return _tool_fail("meridian_get_product", exc)
    except GetProductMCPError as exc:
        return _tool_fail("meridian_get_product", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_get_product", exc)


@function_tool(
    description_override="Search products by natural-language query.",
)
def meridian_search_products(ctx: RunContextWrapper[SupportAgentContext], query: str) -> str:
    """MCP search_products."""
    try:
        return fetch_search_products(acting_customer_id=_cid(ctx), query=query)
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_search_products", exc)
    except SearchProductsValidationError as exc:
        return _tool_fail("meridian_search_products", exc)
    except SearchProductsMCPError as exc:
        return _tool_fail("meridian_search_products", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_search_products", exc)


@function_tool(
    description_override="Lists the signed-in customer’s orders; optional status filter.",
)
def meridian_list_orders(
    ctx: RunContextWrapper[SupportAgentContext],
    customer_id: str | None = None,
    status: str | None = None,
) -> str:
    """MCP list_orders — scoped to session customer."""
    try:
        return fetch_list_orders(
            acting_customer_id=_cid(ctx),
            customer_id=customer_id,
            status=status,
        )
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_list_orders", exc)
    except ListOrdersValidationError as exc:
        return _tool_fail("meridian_list_orders", exc)
    except (GetCustomerAccessError, GetCustomerValidationError) as exc:
        return _tool_fail("meridian_list_orders", exc)
    except ListOrdersMCPError as exc:
        return _tool_fail("meridian_list_orders", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_list_orders", exc)


@function_tool(
    description_override="Fetches one order by UUID; only if it belongs to this customer.",
)
def meridian_get_order(ctx: RunContextWrapper[SupportAgentContext], order_id: str) -> str:
    """MCP get_order."""
    try:
        return fetch_get_order(acting_customer_id=_cid(ctx), order_id=order_id)
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_get_order", exc)
    except GetOrderValidationError as exc:
        return _tool_fail("meridian_get_order", exc)
    except GetOrderNotFoundError as exc:
        return _tool_fail("meridian_get_order", exc)
    except GetOrderAccessError as exc:
        return _tool_fail("meridian_get_order", exc)
    except GetOrderMCPError as exc:
        return _tool_fail("meridian_get_order", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_get_order", exc)


@function_tool(
    description_override=(
        "Creates an order for this customer. Pass items_json: a JSON array of "
        '{ "sku", "quantity", "unit_price", "currency"? }. Only when user explicitly confirms.'
    ),
)
def meridian_create_order(
    ctx: RunContextWrapper[SupportAgentContext],
    items_json: str,
    customer_id: str | None = None,
) -> str:
    """MCP create_order — gated by MERIDIAN_ENABLE_ORDER_SUBMIT."""
    if not _order_submit_enabled():
        return (
            "[meridian_create_order] Order placement is disabled in this environment "
            "(MERIDIAN_ENABLE_ORDER_SUBMIT)."
        )
    raw = (items_json or "").strip()
    if not raw:
        return "[meridian_create_order] items_json is empty."
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"[meridian_create_order] Invalid JSON: {exc}"
    if not isinstance(parsed, list):
        return "[meridian_create_order] items_json must be a JSON array of line items."
    try:
        return fetch_create_order(
            acting_customer_id=_cid(ctx),
            items=parsed,
            customer_id=customer_id,
        )
    except MCPAuthRequired as exc:
        return _tool_fail("meridian_create_order", exc)
    except CreateOrderValidationError as exc:
        return _tool_fail("meridian_create_order", exc)
    except CreateOrderInsufficientInventoryError as exc:
        return _tool_fail("meridian_create_order", exc)
    except CreateOrderProductNotFoundError as exc:
        return _tool_fail("meridian_create_order", exc)
    except CreateOrderCustomerNotFoundError as exc:
        return _tool_fail("meridian_create_order", exc)
    except CreateOrderMCPError as exc:
        return _tool_fail("meridian_create_order", exc)
    except GetCustomerAccessError as exc:
        return _tool_fail("meridian_create_order", exc)
    except Exception as exc:  # noqa: BLE001
        return _tool_fail("meridian_create_order", exc)


def _support_tools() -> list[Any]:
    base: list[Any] = [
        meridian_get_customer,
        meridian_list_products,
        meridian_get_product,
        meridian_search_products,
        meridian_list_orders,
        meridian_get_order,
    ]
    if _order_submit_enabled():
        base.append(meridian_create_order)
    return base


def _parse_support_agent_output(result: Any) -> tuple[SupportAgentResponse, bool]:
    parsed = result.final_output_as(SupportAgentResponse, raise_if_incorrect_type=False)
    if parsed is not None:
        return parsed, True
    out = result.final_output
    text = out if isinstance(out, str) else str(out)
    return (
        SupportAgentResponse(
            reply_markdown=(text or "").strip() or "(empty)",
            tools_were_used=False,
        ),
        False,
    )


def build_support_agent() -> Agent[SupportAgentContext]:
    model = os.environ.get("OPENAI_MERIDIAN_SUPPORT_MODEL", "gpt-4o-mini")
    return Agent[SupportAgentContext](
        name="MeridianSupport",
        instructions=SUPPORT_AGENT_INSTRUCTIONS,
        tools=_support_tools(),
        model=model,
        output_type=SupportAgentResponse,
    )


def run_support_agent_turn(
    conversation: list[dict[str, Any]],
    *,
    acting_customer_id: str,
) -> SupportAgentResponse:
    """
    Run the support agent on the full in-order chat (`role` + `content` per message).

    MCP calls use ``acting_customer_id`` from the verified session.
    """
    t0 = time.perf_counter()
    trace = get_trace_id()
    log_tool_event(
        _LOG,
        logging.INFO,
        "support.agent.turn.start",
        tool="MeridianSupport",
        customer_id_suffix=acting_customer_id[-8:]
        if len(acting_customer_id) >= 8
        else acting_customer_id,
        extra_fields={
            "trace": trace,
            "message_count": len(conversation),
            "create_order_tool": _order_submit_enabled(),
        },
    )
    require_openai_key()
    context = SupportAgentContext(acting_customer_id=acting_customer_id)
    agent = build_support_agent()
    result = Runner.run_sync(
        agent,
        conversation,
        context=context,
        max_turns=32,
    )
    structured, structured_ok = _parse_support_agent_output(result)
    log_tool_event(
        _LOG,
        logging.INFO,
        "support.agent.turn.done",
        tool="MeridianSupport",
        duration_ms=(time.perf_counter() - t0) * 1000,
        customer_id_suffix=acting_customer_id[-8:]
        if len(acting_customer_id) >= 8
        else acting_customer_id,
        extra_fields={
            "trace": trace,
            "reply_chars": len(structured.reply_markdown),
            "tools_were_used": structured.tools_were_used,
            "structured_parse_ok": structured_ok,
        },
    )
    return structured


def stream_support_agent_turn(
    conversation: list[dict[str, Any]],
    *,
    acting_customer_id: str,
) -> tuple[Iterator[str], Callable[[], SupportAgentResponse]]:
    """
    Same as :func:`run_support_agent_turn`, but yield model text deltas as they arrive (for
    ``st.write_stream``). Call the returned ``finalize()`` after the iterator is exhausted to
    obtain the parsed :class:`~meridian.models.SupportAgentResponse` and emit completion logs.
    """
    t0 = time.perf_counter()
    trace = get_trace_id()
    log_tool_event(
        _LOG,
        logging.INFO,
        "support.agent.turn.start",
        tool="MeridianSupport",
        customer_id_suffix=acting_customer_id[-8:]
        if len(acting_customer_id) >= 8
        else acting_customer_id,
        extra_fields={
            "trace": trace,
            "message_count": len(conversation),
            "create_order_tool": _order_submit_enabled(),
            "streaming": True,
        },
    )
    require_openai_key()
    context = SupportAgentContext(acting_customer_id=acting_customer_id)
    agent = build_support_agent()
    chunks, get_streamed = stream_agent_text_chunks(
        agent=agent,
        conversation=conversation,
        context=context,
        max_turns=32,
    )

    def finalize() -> SupportAgentResponse:
        streamed = get_streamed()
        structured, structured_ok = _parse_support_agent_output(streamed)
        log_tool_event(
            _LOG,
            logging.INFO,
            "support.agent.turn.done",
            tool="MeridianSupport",
            duration_ms=(time.perf_counter() - t0) * 1000,
            customer_id_suffix=acting_customer_id[-8:]
            if len(acting_customer_id) >= 8
            else acting_customer_id,
            extra_fields={
                "trace": trace,
                "reply_chars": len(structured.reply_markdown),
                "tools_were_used": structured.tools_were_used,
                "structured_parse_ok": structured_ok,
                "streaming": True,
            },
        )
        return structured

    return chunks, finalize
