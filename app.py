"""Meridian Electronics — support chatbot with OpenAI Agents sign-in and gated MCP calls."""

from __future__ import annotations

import os
import time

import streamlit as st

from meridian.auth_agent import MeridianAuthContext, run_auth_agent_turn
from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.principal import (
    principal_from_session_dict,
    principal_to_session_dict,
)

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()

SESSION_CUSTOMER = "meridian_authenticated_customer"
SESSION_AUTH_MESSAGES = "meridian_auth_chat_messages"
SESSION_AUTH_CONTEXT = "meridian_auth_context"


def stream_text(text: str, chunk_chars: int = 8):
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
        time.sleep(0.02)


def _ensure_chat_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _ensure_auth_state():
    if SESSION_AUTH_CONTEXT not in st.session_state:
        st.session_state[SESSION_AUTH_CONTEXT] = MeridianAuthContext()
    if SESSION_AUTH_MESSAGES not in st.session_state:
        st.session_state[SESSION_AUTH_MESSAGES] = []


def _reset_auth_state():
    st.session_state[SESSION_AUTH_MESSAGES] = []
    st.session_state[SESSION_AUTH_CONTEXT] = MeridianAuthContext()


def mock_reply(user_text: str, customer_id: str, label: str) -> str:
    """Demo support reply; MCP calls go only through the guarded client."""
    try:
        inv = tool_result_text(
            call_tool_sync_guarded(
                "list_products",
                {"is_active": True},
                acting_customer_id=customer_id,
            )
        )
        inv_preview = inv[:900] + ("…" if len(inv) > 900 else "")
    except MCPAuthRequired as exc:
        inv_preview = str(exc)
    except Exception as exc:  # noqa: BLE001
        inv_preview = f"Inventory call failed ({type(exc).__name__}): {exc}"

    return (
        f"_Signed in as {label}._\n\n"
        f"**Your message:** {user_text[:280]}{'…' if len(user_text) > 280 else ''}\n\n"
        "**Active products (first chunk via MCP, gated by session):**\n\n"
        f"```text\n{inv_preview}\n```"
    )


def _sidebar_signed_in(customer):
    st.sidebar.header("Account")
    st.sidebar.success(f"Signed in as **{customer.display_name}**")
    st.sidebar.caption(customer.email)
    with st.sidebar.expander("Profile from MCP"):
        st.markdown(customer.details_text or "_No detail text stored._")
    if st.sidebar.button("Sign out", type="secondary"):
        st.session_state[SESSION_CUSTOMER] = None
        st.session_state.messages = []
        _reset_auth_state()
        st.rerun()


def _sidebar_auth_gate():
    st.sidebar.header("Account")
    st.sidebar.markdown(
        "The **AI sign-in assistant** (OpenAI Agents) will ask for your **email** and "
        "**4-digit account PIN** (Meridian stores a numeric PIN; you may call it a password). "
        "No catalog or order MCP calls run until verification succeeds."
    )
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        st.sidebar.warning("Set **OPENAI_API_KEY** in `.env` or the environment to enable sign-in.")


def _render_auth_chat():
    st.subheader("Sign in with the assistant")
    st.caption("Chat below — the agent will guide you and call Meridian verification when ready.")

    for msg in st.session_state[SESSION_AUTH_MESSAGES]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        st.error("Add **OPENAI_API_KEY** to use the sign-in agent.")
        return

    ctx: MeridianAuthContext = st.session_state[SESSION_AUTH_CONTEXT]

    if prompt := st.chat_input("Talk to the sign-in assistant…", key="meridian_auth_chat_input"):
        st.session_state[SESSION_AUTH_MESSAGES].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        try:
            reply = run_auth_agent_turn(
                st.session_state[SESSION_AUTH_MESSAGES],
                context=ctx,
            )
        except RuntimeError as exc:
            reply = f"**Configuration error:** {exc}"
        except Exception as exc:  # noqa: BLE001
            reply = f"**Sign-in assistant error:** {type(exc).__name__}: {exc}"

        st.session_state[SESSION_AUTH_MESSAGES].append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)

        principal = ctx.pending_principal
        if principal is not None:
            st.session_state[SESSION_CUSTOMER] = principal_to_session_dict(principal)
            ctx.pending_principal = None
            st.session_state.messages = []
            st.session_state[SESSION_AUTH_MESSAGES] = []
            st.success("You’re signed in. Loading support chat…")
            st.rerun()


def main():
    os.environ.setdefault(
        "MCP_SERVER_URL",
        "https://order-mcp-74afyau24q-uc.a.run.app/mcp",
    )
    st.set_page_config(
        page_title="Meridian Support",
        page_icon="🛒",
        layout="centered",
    )

    _ensure_chat_state()
    _ensure_auth_state()

    customer = None
    if st.session_state.get(SESSION_CUSTOMER):
        customer = principal_from_session_dict(st.session_state[SESSION_CUSTOMER])

    st.title("Meridian Electronics — support")
    st.caption("OpenAI Agents for sign-in; MCP calls require a verified customer session.")

    if customer is not None:
        _sidebar_signed_in(customer)
        st.success(f"Session active for **{customer.display_name}**.")
        with st.expander("Customer id (for support logs)"):
            st.code(customer.customer_id)

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        label = f"{customer.display_name} <{customer.email}>"
        if prompt := st.chat_input("Ask about products, orders, or shipping…"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            reply = mock_reply(prompt, customer.customer_id, label)
            with st.chat_message("assistant"):
                st.write_stream(stream_text(reply))
            st.session_state.messages.append({"role": "assistant", "content": reply})
        return

    _sidebar_auth_gate()
    _render_auth_chat()


if __name__ == "__main__":
    main()
