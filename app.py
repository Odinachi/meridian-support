"""Meridian Electronics — support chatbot with OpenAI Agents sign-in and gated MCP calls."""

from __future__ import annotations

import logging
import os
import re
import time

import streamlit as st

from meridian.auth_agent import MeridianAuthContext, run_auth_agent_turn
from meridian.auth_audit import email_domain_only
from meridian.mcp_guard import MCPAuthRequired
from meridian.logout_intent import looks_like_logout_request, parse_logout_confirmation
from meridian.observability import configure_logging, log_tool_event, new_trace_id, trace_scope
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

_LOG = logging.getLogger("meridian.app")

SESSION_CUSTOMER = "meridian_authenticated_customer"
SESSION_AUTH_MESSAGES = "meridian_auth_chat_messages"
SESSION_AUTH_CONTEXT = "meridian_auth_context"
SESSION_LOGOUT_PENDING = "meridian_logout_confirm_pending"


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


_SKU_IN_MESSAGE = re.compile(r"\b([A-Za-z]{3}-\d{4})\b")


def _skus_from_user_message(text: str, *, limit: int = 3) -> list[str]:
    """First few unique SKUs mentioned (e.g. COM-0001), order preserved, max ``limit``."""
    out: list[str] = []
    for m in _SKU_IN_MESSAGE.finditer(text or ""):
        sku = m.group(1).upper()
        if sku not in out:
            out.append(sku)
        if len(out) >= limit:
            break
    return out


def mock_reply(customer_id: str, user_message: str = "") -> str:
    """Demo support reply: optional ``get_product`` by SKU from the message, plus ``list_products``."""
    from meridian.tools.get_product import (
        GetProductMCPError,
        GetProductNotFoundError,
        GetProductValidationError,
        fetch_get_product,
    )
    from meridian.tools.list_products import (
        ListProductsMCPError,
        ListProductsValidationError,
        fetch_list_products,
    )

    sku_blocks: list[str] = []
    for sku in _skus_from_user_message(user_message):
        try:
            detail = fetch_get_product(acting_customer_id=customer_id, sku=sku)
            clip = detail[:4000] + ("…" if len(detail) > 4000 else "")
            sku_blocks.append(f"**{sku}**\n```text\n{clip}\n```")
        except GetProductValidationError as exc:
            sku_blocks.append(f"**{sku}** — {exc}")
        except GetProductNotFoundError as exc:
            sku_blocks.append(f"**{sku}** — {exc}")
        except GetProductMCPError as exc:
            sku_blocks.append(f"**{sku}** — {exc}")
        except MCPAuthRequired as exc:
            sku_blocks.append(f"**{sku}** — {exc}")

    try:
        inv = fetch_list_products(
            acting_customer_id=customer_id,
            category=None,
            is_active=None,
        )
        inv_preview = inv[:900] + ("…" if len(inv) > 900 else "")
    except ListProductsValidationError as exc:
        inv_preview = str(exc)
    except MCPAuthRequired as exc:
        inv_preview = str(exc)
    except ListProductsMCPError as exc:
        inv_preview = str(exc)
    except Exception as exc:  # noqa: BLE001
        inv_preview = f"Couldn't load inventory ({type(exc).__name__})."

    intro = (
        "We're not answering end-to-end yet—this build still routes through a demo path. "
        "Below is live catalog data for your signed-in session."
    )
    sku_section = ""
    if sku_blocks:
        sku_section = "**SKU lookup** (from your message)\n\n" + "\n\n".join(sku_blocks) + "\n\n---\n\n"
    list_section = f"**In-stock snapshot**\n\n```text\n{inv_preview}\n```"
    return intro + "\n\n" + sku_section + list_section


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
                st.markdown(reply)

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

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("How can we help?"):
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
                    reply = mock_reply(customer.customer_id, prompt)
                    with st.chat_message("assistant"):
                        st.write_stream(stream_text(reply))
                    st.session_state.messages.append({"role": "assistant", "content": reply})
        return

    _sidebar_auth_gate()
    _render_auth_chat()


if __name__ == "__main__":
    main()
