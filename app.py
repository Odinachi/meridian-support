"""Meridian Electronics — support chatbot with OpenAI Agents sign-in and gated MCP calls."""

from __future__ import annotations

import os
import time

import streamlit as st

from meridian.auth_agent import MeridianAuthContext, run_auth_agent_turn
from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded
from meridian.logout_intent import looks_like_logout_request, parse_logout_confirmation
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
SESSION_LOGOUT_PENDING = "meridian_logout_confirm_pending"


def _perform_sign_out():
    st.session_state[SESSION_CUSTOMER] = None
    st.session_state[SESSION_LOGOUT_PENDING] = False
    st.session_state.messages = []
    _reset_auth_state()


def stream_text(text: str, chunk_chars: int = 8):
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
        time.sleep(0.02)


def _ensure_chat_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if SESSION_LOGOUT_PENDING not in st.session_state:
        st.session_state[SESSION_LOGOUT_PENDING] = False


def _ensure_auth_state():
    if SESSION_AUTH_CONTEXT not in st.session_state:
        st.session_state[SESSION_AUTH_CONTEXT] = MeridianAuthContext()
    if SESSION_AUTH_MESSAGES not in st.session_state:
        st.session_state[SESSION_AUTH_MESSAGES] = []


def _reset_auth_state():
    st.session_state[SESSION_AUTH_MESSAGES] = []
    st.session_state[SESSION_AUTH_CONTEXT] = MeridianAuthContext()


def mock_reply(customer_id: str) -> str:
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
        inv_preview = f"Couldn't load inventory ({type(exc).__name__})."

    return (
        "We're not answering end-to-end yet—this build still routes through a demo path. "
        "Here's a current slice of **in-stock catalog** so you can see live data behind the session:\n\n"
        f"```text\n{inv_preview}\n```"
    )


def _sidebar_signed_in(customer):
    st.sidebar.header("Account")
    st.sidebar.success(f"**{customer.display_name}**")
    st.sidebar.caption(customer.email)
    with st.sidebar.expander("Account details"):
        st.markdown(customer.details_text or "No profile text returned.")
    if st.sidebar.button("Sign out", type="secondary"):
        _perform_sign_out()
        st.rerun()


def _sidebar_auth_gate():
    st.sidebar.header("Account")
  
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        st.sidebar.warning("Add `OPENAI_API_KEY` to `.env` to turn on chat-based sign-in.")


def _render_auth_chat():
    st.subheader("Sign in")
    st.caption("Use the chat—same flow you'd expect from a support line, without the hold music.")

    for msg in st.session_state[SESSION_AUTH_MESSAGES]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        st.error("Chat sign-in is off until `OPENAI_API_KEY` is set in your environment.")
        return

    ctx: MeridianAuthContext = st.session_state[SESSION_AUTH_CONTEXT]

    if prompt := st.chat_input("Message…", key="meridian_auth_chat_input"):
        st.session_state[SESSION_AUTH_MESSAGES].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        try:
            reply = run_auth_agent_turn(
                st.session_state[SESSION_AUTH_MESSAGES],
                context=ctx,
            )
        except RuntimeError as exc:
            reply = f"Setup issue: {exc}"
        except Exception as exc:  # noqa: BLE001
            reply = f"Something went wrong ({type(exc).__name__}). Try again in a moment."

        st.session_state[SESSION_AUTH_MESSAGES].append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)

        principal = ctx.pending_principal
        if principal is not None:
            st.session_state[SESSION_CUSTOMER] = principal_to_session_dict(principal)
            ctx.pending_principal = None
            st.session_state.messages = []
            st.session_state[SESSION_AUTH_MESSAGES] = []
            st.success("Verified. Opening support…")
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

    st.title("Meridian support")
    st.caption("Electronics orders & account help—sign in first, then ask anything.")

    if customer is not None:
        _sidebar_signed_in(customer)
        st.success(f"**{customer.display_name}** — you're in.")
        with st.expander("Reference ID (for tickets)"):
            st.code(customer.customer_id)

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("How can we help?"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            if st.session_state[SESSION_LOGOUT_PENDING]:
                decision = parse_logout_confirmation(prompt)
                if decision == "yes":
                    reply = "You're signed out. When you're ready, sign in again from here."
                    st.session_state.messages.append({"role": "assistant", "content": reply})
                    with st.chat_message("assistant"):
                        st.write_stream(stream_text(reply))
                    _perform_sign_out()
                    st.rerun()
                elif decision == "no":
                    st.session_state[SESSION_LOGOUT_PENDING] = False
                    reply = "Understood—you're still signed in. What else do you need?"
                    with st.chat_message("assistant"):
                        st.write_stream(stream_text(reply))
                    st.session_state.messages.append({"role": "assistant", "content": reply})
                else:
                    reply = "Sign out—yes or no?"
                    with st.chat_message("assistant"):
                        st.write_stream(stream_text(reply))
                    st.session_state.messages.append({"role": "assistant", "content": reply})
            elif looks_like_logout_request(prompt):
                st.session_state[SESSION_LOGOUT_PENDING] = True
                reply = "End this session? Reply yes to sign out, or no to stay."
                with st.chat_message("assistant"):
                    st.write_stream(stream_text(reply))
                st.session_state.messages.append({"role": "assistant", "content": reply})
            else:
                reply = mock_reply(customer.customer_id)
                with st.chat_message("assistant"):
                    st.write_stream(stream_text(reply))
                st.session_state.messages.append({"role": "assistant", "content": reply})
        return

    _sidebar_auth_gate()
    _render_auth_chat()


if __name__ == "__main__":
    main()
