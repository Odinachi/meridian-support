import pytest

from meridian.auth_agent import build_auth_agent
from meridian.models import AuthAgentResponse
from meridian.auth import (
    AuthError,
    normalize_email,
    validate_pin_format,
    verify_customer_pin,
    _parse_customer_block,
)
from meridian.principal import (
    CustomerPrincipal,
    principal_from_session_dict,
    principal_to_session_dict,
)


def test_auth_agent_response_model():
    r = AuthAgentResponse(reply_markdown="Hi")
    assert r.reply_markdown == "Hi"


def test_build_auth_agent_structured_output():
    agent = build_auth_agent()
    assert agent.output_type is AuthAgentResponse


def test_normalize_email():
    assert normalize_email("  Jason@Example.COM ") == "jason@example.com"


def test_validate_pin_format():
    validate_pin_format("0000")
    with pytest.raises(AuthError):
        validate_pin_format("12")
    with pytest.raises(AuthError):
        validate_pin_format("12ab")


def test_parse_customer_block_with_explicit_id():
    text = """Customer ID: 004cebc1-65d8-4190-99fb-30245b11bc1c
Customer: Jason Valencia
Email: jason31@example.com
Role: buyer
"""
    p = _parse_customer_block(text, "fallback@example.com")
    assert p.customer_id == "004cebc1-65d8-4190-99fb-30245b11bc1c"
    assert p.email == "jason31@example.com"
    assert p.display_name == "Jason Valencia"


def test_parse_customer_block_uuid_fallback():
    text = """Verified.

004cebc1-65d8-4190-99fb-30245b11bc1c

Some other uuid 11111111-1111-1111-1111-111111111111
"""
    p = _parse_customer_block(text, "u@example.com")
    assert p.customer_id == "004cebc1-65d8-4190-99fb-30245b11bc1c"
    assert p.email == "u@example.com"


def test_principal_roundtrip():
    p = CustomerPrincipal(
        customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c",
        email="a@b.co",
        display_name="A",
        details_text="x",
    )
    d = principal_to_session_dict(p)
    assert principal_from_session_dict(d) == p


def test_verify_customer_pin_maps_mcp_failure(monkeypatch):
    from types import SimpleNamespace

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[
                SimpleNamespace(
                    text="Error executing tool verify_customer_pin: "
                    "Customer not found or PIN incorrect"
                )
            ],
        )

    monkeypatch.setattr("meridian.auth.call_tool_sync_guarded", fake_guarded)
    with pytest.raises(AuthError, match="didn't match"):
        verify_customer_pin("any@example.com", "1234")


def test_verify_customer_pin_success(monkeypatch):
    from types import SimpleNamespace

    body = """Customer ID: aa11bb22-cc33-dd44-ee55-ff6677889900
Customer: Test User
Email: test.user@example.com
Role: buyer
"""

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=body)])

    monkeypatch.setattr("meridian.auth.call_tool_sync_guarded", fake_guarded)
    p = verify_customer_pin("TEST.USER@Example.com", "9876")
    assert p.customer_id == "aa11bb22-cc33-dd44-ee55-ff6677889900"
    assert p.display_name == "Test User"
    assert p.email == "test.user@example.com"
