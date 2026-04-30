"""OpenAI Agents SDK — conversational sign-in (email + account PIN)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from agents.agent import Agent
from agents.run import Runner
from agents.run_context import RunContextWrapper
from agents.tool import function_tool

from meridian.auth import AuthError, verify_customer_pin
from meridian.principal import CustomerPrincipal


@dataclass
class MeridianAuthContext:
    """Mutable run context shared with tools (one instance per Streamlit session)."""

    pending_principal: CustomerPrincipal | None = None
    last_tool_user_visible: str | None = field(default=None, repr=False)


AUTH_AGENT_INSTRUCTIONS = """You are the Meridian Electronics **sign-in assistant** only.

Your job:
1. Politely collect the customer’s **email** on their Meridian account and their **4-digit account PIN**.
   Customers may call the PIN a "password" or "security code" — it is always **exactly four digits** (0–9).
2. When (and only when) you have a plausible email and exactly four digits, call the tool
   `submit_meridian_credentials` **once** with those values.
3. If the tool reports failure, give a short, neutral message (do not say whether the email exists).
4. Do **not** answer questions about products, orders, shipping, or inventory before sign-in succeeds.
   If asked, explain that you must verify their account first, then the support assistant can help.
5. Never invent or guess a PIN. Never repeat a PIN back in full once collected.
6. Keep replies concise and professional.
"""


@function_tool(
    description_override=(
        "Verify the customer’s Meridian account using their email and 4-digit numeric PIN "
        "(users may refer to it as their account password). Call only when both are known."
    ),
)
def submit_meridian_credentials(
    ctx: RunContextWrapper[MeridianAuthContext],
    email: str,
    account_password: str,
) -> str:
    """Verify email and 4-digit PIN with the Meridian order service."""
    try:
        principal = verify_customer_pin(email, account_password)
    except AuthError as exc:
        ctx.context.pending_principal = None
        msg = f"VERIFICATION_FAILED: {exc}"
        ctx.context.last_tool_user_visible = msg
        return msg

    ctx.context.pending_principal = principal
    ok = (
        f"VERIFICATION_OK: Signed in as {principal.display_name}. "
        "Briefly welcome them; they will reach the main support assistant next."
    )
    ctx.context.last_tool_user_visible = ok
    return ok


def build_auth_agent() -> Agent[MeridianAuthContext]:
    model = os.environ.get("OPENAI_MERIDIAN_AUTH_MODEL", "gpt-4o-mini")
    return Agent[MeridianAuthContext](
        name="MeridianSignIn",
        instructions=AUTH_AGENT_INSTRUCTIONS,
        tools=[submit_meridian_credentials],
        model=model,
    )


def require_openai_key() -> str:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to your environment or `.env` file "
            "to use the AI sign-in assistant."
        )
    return key


def run_auth_agent_turn(
    conversation: list[dict[str, Any]],
    *,
    context: MeridianAuthContext,
) -> str:
    """
    Run the auth agent on the full in-order chat (`role` + `content` per message).

    The last message should be the latest user turn; include prior user/assistant
    strings so the model retains context (no separate DB session required).
    """
    require_openai_key()
    agent = build_auth_agent()
    result = Runner.run_sync(
        agent,
        conversation,
        context=context,
        max_turns=16,
    )
    out = result.final_output
    return out if isinstance(out, str) else str(out)
