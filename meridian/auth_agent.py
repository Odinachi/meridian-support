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


AUTH_AGENT_INSTRUCTIONS = """You work for Meridian Electronics and only handle account verification before support.

Tone: calm, direct, human—like a good front desk. No exclamation stacks, no “I’d be happy to,” no emojis.

Collect the email on the Meridian account and the **four-digit PIN** Meridian uses for this channel (not their email password). People might say “password” or “code”; it’s still four digits only.

Call `submit_meridian_credentials` once you have both. If it fails, say something short and neutral—don’t hint whether the email exists.

Until verification succeeds, don’t answer product, order, or shipping questions; say they’ll get that right after sign-in.

Never invent a PIN or read one back aloud in full. Keep answers short.
"""


@function_tool(
    description_override=(
        "Checks email plus Meridian’s four-digit support PIN. "
        "Call only when you have both; PIN is digits only."
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
        f"VERIFICATION_OK: {principal.display_name} is verified. "
        "One short welcome—they’re about to use main support."
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
            "OPENAI_API_KEY is missing—add it to `.env` or your shell so chat sign-in can run."
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
