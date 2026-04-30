import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import list_products as lp


def test_category_strip_and_empty():
    assert lp._sanitize_category("  Monitors  ") == "Monitors"
    assert lp._sanitize_category("   ") is None
    assert lp._sanitize_category(None) is None


def test_category_rejects_control_characters():
    with pytest.raises(lp.ListProductsValidationError):
        lp._sanitize_category("bad\nline")


def test_category_rejects_non_string():
    with pytest.raises(lp.ListProductsValidationError):
        lp._sanitize_category(12)  # type: ignore[arg-type]


def test_category_max_length():
    with pytest.raises(lp.ListProductsValidationError):
        lp._sanitize_category("x" * 200)


def test_is_active_strict_bool():
    assert lp._sanitize_is_active(True) is True
    assert lp._sanitize_is_active(False) is False
    assert lp._sanitize_is_active(None) is None
    with pytest.raises(lp.ListProductsValidationError):
        lp._sanitize_is_active("true")  # type: ignore[arg-type]


def test_build_mcp_arguments_omits_none():
    assert lp._build_mcp_arguments(category=None, is_active=None) == {}
    assert lp._build_mcp_arguments(category="Monitors", is_active=True) == {
        "category": "Monitors",
        "is_active": True,
    }


def test_fetch_success(monkeypatch):
    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "list_products"
        assert arguments == {"category": "Monitors", "is_active": True}
        assert acting_customer_id == "cust-uuid-here"
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="SKU-1 widget")],
        )

    monkeypatch.setattr(lp, "call_tool_sync_guarded", fake_guarded)
    out = lp.fetch_list_products(
        acting_customer_id="cust-uuid-here",
        category="Monitors",
        is_active=True,
    )
    assert "SKU-1" in out


def test_fetch_mcp_error(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool list_products: boom")],
        )

    monkeypatch.setattr(lp, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(lp.ListProductsMCPError):
        lp.fetch_list_products(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c")


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(lp, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        lp.fetch_list_products(acting_customer_id="")


def test_response_truncation(monkeypatch):
    body = "a" * 5000

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text=body)],
        )

    monkeypatch.setattr(lp, "call_tool_sync_guarded", fake_guarded)
    out = lp.fetch_list_products(
        acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c",
        max_response_chars=100,
    )
    assert len(out) <= 150
    assert "truncated" in out.lower()


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(lp, "call_tool_sync_guarded", fake_guarded)
    lp.fetch_list_products(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c")
    assert any("list_products.success" in r.message for r in caplog.records)
