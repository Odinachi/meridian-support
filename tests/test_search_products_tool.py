import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import search_products as sp


def test_sanitize_query():
    assert sp.sanitize_query("  curved  monitor ") == "curved monitor"


def test_sanitize_empty():
    with pytest.raises(sp.SearchProductsValidationError):
        sp.sanitize_query("   ")


def test_sanitize_too_long():
    with pytest.raises(sp.SearchProductsValidationError):
        sp.sanitize_query("x" * 300)


def test_sanitize_control_char():
    with pytest.raises(sp.SearchProductsValidationError):
        sp.sanitize_query("bad\x01")


def test_sanitize_non_string():
    with pytest.raises(sp.SearchProductsValidationError):
        sp.sanitize_query(1)  # type: ignore[arg-type]


def test_fetch_success(monkeypatch):
    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "search_products"
        assert arguments == {"query": "dock"}
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="SKU-1 Dock")],
        )

    monkeypatch.setattr(sp, "call_tool_sync_guarded", fake_guarded)
    out = sp.fetch_search_products(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c", query="  dock ")
    assert "Dock" in out


def test_fetch_mcp_error(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool search_products: boom")],
        )

    monkeypatch.setattr(sp, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(sp.SearchProductsMCPError):
        sp.fetch_search_products(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c", query="x")


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(sp, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        sp.fetch_search_products(acting_customer_id="", query="mouse")


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(sp, "call_tool_sync_guarded", fake_guarded)
    sp.fetch_search_products(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c", query="hub")
    assert any("search_products.success" in r.message for r in caplog.records)
