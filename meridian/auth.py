"""Customer authentication via MCP `verify_customer_pin`."""

from __future__ import annotations

import re

from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import call_tool_sync_guarded
from meridian.principal import CustomerPrincipal


class AuthError(Exception):
    """PIN verification failed or MCP returned an error."""


class AuthResponseParseError(AuthError):
    """Verified response did not contain a customer id we could parse."""


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def normalize_email(email: str) -> str:
    return " ".join(email.split()).lower()


def validate_pin_format(pin: str) -> None:
    if not re.fullmatch(r"\d{4}", pin or ""):
        raise AuthError("PIN must be exactly 4 digits.")


def _parse_customer_block(text: str, fallback_email: str) -> CustomerPrincipal:
    customer_id = None
    m = re.search(r"Customer\s*ID:\s*([0-9a-fA-F-]{36})", text)
    if m:
        customer_id = m.group(1).lower()
    if not customer_id:
        matches = _UUID_RE.findall(text)
        if matches:
            customer_id = matches[0].lower()
    if not customer_id:
        raise AuthResponseParseError(
            "Sign-in succeeded but the response did not include a customer id. "
            "Contact engineering with the request timestamp."
        )

    display_name = ""
    m = re.search(r"^Customer:\s*(.+)$", text, re.MULTILINE)
    if m:
        display_name = m.group(1).strip()

    email_found = fallback_email
    m = re.search(r"^Email:\s*(\S+)\s*$", text, re.MULTILINE)
    if m:
        email_found = normalize_email(m.group(1))

    if not display_name:
        display_name = email_found.split("@")[0]

    return CustomerPrincipal(
        customer_id=customer_id,
        email=email_found,
        display_name=display_name,
        details_text=text.strip(),
    )


def verify_customer_pin(email: str, pin: str) -> CustomerPrincipal:
    """
    Verify email + 4-digit PIN against the order MCP server.

    This is the only MCP path allowed without an existing customer session (see `mcp_guard`).
    """
    validate_pin_format(pin)
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        raise AuthError("Enter a valid email address.")

    try:
        result = call_tool_sync_guarded(
            "verify_customer_pin",
            {"email": normalized, "pin": pin},
            acting_customer_id=None,
        )
    except Exception as exc:  # noqa: BLE001 — surface to UI, log in production
        raise AuthError(f"Could not reach sign-in service ({type(exc).__name__}).") from exc

    text = tool_result_text(result)
    if result.isError or "Error executing tool" in text:
        if "Customer not found" in text or "PIN incorrect" in text:
            raise AuthError("Email or PIN is incorrect.")
        raise AuthError(text or "Sign-in failed.")

    return _parse_customer_block(text, normalized)
