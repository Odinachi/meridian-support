"""Meridian Electronics — support chatbot with OpenAI Agents sign-in and gated MCP calls."""

from __future__ import annotations

import logging
import os

import streamlit as st

from meridian.agent_streaming import iter_static_text_chunks
from meridian.auth_agent import MeridianAuthContext, run_auth_agent_turn
from meridian.auth_audit import email_domain_only
from meridian.logout_intent import looks_like_logout_request, parse_logout_confirmation
from meridian.observability import configure_logging, log_tool_event, new_trace_id, trace_scope
from meridian.principal import (
    principal_from_session_dict,
    principal_to_session_dict,
)
from meridian.support_agent import stream_support_agent_turn

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()

_LOG = logging.getLogger("meridian.app")

SESSION_CUSTOMER = "meridian_authenticated_customer"
SESSION_AUTH_MESSAGES = "meridian_auth_chat_messages"
SESSION_AUTH_CONTEXT = "meridian_auth_context"
SESSION_LOGOUT_PENDING = "meridian_logout_confirm_pending"


def _openai_configured() -> bool:
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())


def _perform_sign_out():
    with trace_scope(new_trace_id()):
        had_customer = bool(st.session_state.get(SESSION_CUSTOMER))
        log_tool_event(
            _LOG,
            logging.INFO,
            "auth.session.sign_out",
            tool="session",
            extra_fields={"had_customer": had_customer},
        )
        st.session_state[SESSION_CUSTOMER] = None
        st.session_state[SESSION_LOGOUT_PENDING] = False
        st.session_state.messages = []
        _reset_auth_state()


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

    if not _openai_configured():
        st.sidebar.warning("Add `OPENAI_API_KEY` to `.env` to turn on chat-based sign-in.")


def _render_auth_chat():
    st.subheader("Sign in")
    st.caption("Use the chat—same flow you'd expect from a support line, without the hold music.")

    for msg in st.session_state[SESSION_AUTH_MESSAGES]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not _openai_configured():
        st.error("Chat sign-in is off until `OPENAI_API_KEY` is set in your environment.")
        return

    ctx: MeridianAuthContext = st.session_state[SESSION_AUTH_CONTEXT]

    if prompt := st.chat_input("Message…", key="meridian_auth_chat_input"):
        with trace_scope(new_trace_id()):
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
                st.write_stream(iter_static_text_chunks(reply))

            principal = ctx.pending_principal
            if principal is not None:
                suffix = (
                    principal.customer_id[-8:]
                    if len(principal.customer_id) >= 8
                    else principal.customer_id
                )
                log_tool_event(
                    _LOG,
                    logging.INFO,
                    "auth.session.established",
                    tool="session",
                    customer_id_suffix=suffix,
                    extra_fields={"email_domain": email_domain_only(principal.email)},
                )
                st.session_state[SESSION_CUSTOMER] = principal_to_session_dict(principal)
                ctx.pending_principal = None
                st.session_state.messages = []
                st.session_state[SESSION_AUTH_MESSAGES] = []
                st.success("Verified. Opening support…")
                st.rerun()


def main():
    configure_logging()
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

        if not _openai_configured():
            st.warning(
                "Support chat needs **`OPENAI_API_KEY`** (same key as sign-in). "
                "Add it to `.env` and reload this page."
            )

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("How can we help?", disabled=not _openai_configured()):
            with trace_scope(new_trace_id()):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)

                if st.session_state[SESSION_LOGOUT_PENDING]:
                    decision = parse_logout_confirmation(prompt)
                    if decision == "yes":
                        reply = "You're signed out. When you're ready, sign in again from here."
                        st.session_state.messages.append({"role": "assistant", "content": reply})
                        with st.chat_message("assistant"):
                            st.write_stream(iter_static_text_chunks(reply))
                        _perform_sign_out()
                        st.rerun()
                    elif decision == "no":
                        st.session_state[SESSION_LOGOUT_PENDING] = False
                        reply = "Understood—you're still signed in. What else do you need?"
                        with st.chat_message("assistant"):
                            st.write_stream(iter_static_text_chunks(reply))
                        st.session_state.messages.append({"role": "assistant", "content": reply})
                    else:
                        reply = "Sign out—yes or no?"
                        with st.chat_message("assistant"):
                            st.write_stream(iter_static_text_chunks(reply))
                        st.session_state.messages.append({"role": "assistant", "content": reply})
                elif looks_like_logout_request(prompt):
                    st.session_state[SESSION_LOGOUT_PENDING] = True
                    reply = "End this session? Reply yes to sign out, or no to stay."
                    with st.chat_message("assistant"):
                        st.write_stream(iter_static_text_chunks(reply))
                    st.session_state.messages.append({"role": "assistant", "content": reply})
                else:
                    with st.chat_message("assistant"):
                        try:
                            chunks, finalize_support = stream_support_agent_turn(
                                st.session_state.messages,
                                acting_customer_id=customer.customer_id,
                            )
                            st.write_stream(chunks)
                            turn = finalize_support()
                            reply = turn.reply_markdown
                        except RuntimeError as exc:
                            reply = f"Setup issue: {exc}"
                            st.write_stream(iter_static_text_chunks(reply))
                        except Exception as exc:  # noqa: BLE001
                            reply = (
                                f"Support agent hit an error ({type(exc).__name__}). "
                                "Check `OPENAI_API_KEY` and MCP connectivity, then try again."
                            )
                            st.write_stream(iter_static_text_chunks(reply))
                    st.session_state.messages.append({"role": "assistant", "content": reply})
        return

    _sidebar_auth_gate()
    _render_auth_chat()


if __name__ == "__main__":
    main()
