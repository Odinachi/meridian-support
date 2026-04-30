"""Customer authentication via MCP `verify_customer_pin`."""

from __future__ import annotations

import logging
import re
import time

from meridian.auth_audit import email_domain_only
from meridian.mcp_client import tool_result_text
from meridian.mcp_guard import call_tool_sync_guarded
from meridian.observability import get_trace_id, log_tool_event
from meridian.principal import CustomerPrincipal

_LOG = logging.getLogger("meridian.auth")


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
        raise AuthError("PIN should be four digits—numbers only.")


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
            "Sign-in worked, but we couldn’t read your account id. Contact Meridian IT with the time you tried."
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
    t0 = time.perf_counter()
    trace = get_trace_id()

    try:
        validate_pin_format(pin)
    except AuthError:
        log_tool_event(
            _LOG,
            logging.INFO,
            "auth.verify.input_rejected",
            tool="verify_customer_pin",
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra_fields={"trace": trace, "reason": "pin_format"},
        )
        raise

    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        log_tool_event(
            _LOG,
            logging.INFO,
            "auth.verify.input_rejected",
            tool="verify_customer_pin",
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra_fields={"trace": trace, "reason": "email_shape"},
        )
        raise AuthError("That doesn't look like a complete email address.")

    domain = email_domain_only(normalized)
    log_tool_event(
        _LOG,
        logging.INFO,
        "auth.verify.mcp_call",
        tool="verify_customer_pin",
        extra_fields={"trace": trace, "email_domain": domain},
    )

    try:
        result = call_tool_sync_guarded(
            "verify_customer_pin",
            {"email": normalized, "pin": pin},
            acting_customer_id=None,
        )
    except Exception as exc:  # noqa: BLE001 — surface to UI, log in production
        log_tool_event(
            _LOG,
            logging.ERROR,
            "auth.verify.transport_error",
            tool="verify_customer_pin",
            duration_ms=(time.perf_counter() - t0) * 1000,
            extra_fields={"trace": trace, "email_domain": domain, "exc_type": type(exc).__name__},
        )
        _LOG.exception("verify_customer_pin MCP transport failed")
        raise AuthError("We couldn't reach Meridian sign-in. Try again shortly.") from exc

    text = tool_result_text(result)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if result.isError or "Error executing tool" in text:
        if "Customer not found" in text or "PIN incorrect" in text:
            log_tool_event(
                _LOG,
                logging.INFO,
                "auth.verify.failed_credentials",
                tool="verify_customer_pin",
                duration_ms=elapsed_ms,
                extra_fields={"trace": trace, "email_domain": domain},
            )
            raise AuthError("Email or PIN didn't match. Try again.")
        log_tool_event(
            _LOG,
            logging.WARNING,
            "auth.verify.mcp_error",
            tool="verify_customer_pin",
            duration_ms=elapsed_ms,
            extra_fields={"trace": trace, "email_domain": domain},
        )
        raise AuthError(text or "Sign-in didn't go through.")

    try:
        principal = _parse_customer_block(text, normalized)
    except AuthResponseParseError:
        log_tool_event(
            _LOG,
            logging.ERROR,
            "auth.verify.parse_failed",
            tool="verify_customer_pin",
            duration_ms=elapsed_ms,
            extra_fields={"trace": trace, "email_domain": domain},
        )
        raise

    suffix = principal.customer_id[-8:] if len(principal.customer_id) >= 8 else principal.customer_id
    log_tool_event(
        _LOG,
        logging.INFO,
        "auth.verify.success",
        tool="verify_customer_pin",
        duration_ms=elapsed_ms,
        customer_id_suffix=suffix,
        extra_fields={"trace": trace, "email_domain": domain},
    )
    return principal
