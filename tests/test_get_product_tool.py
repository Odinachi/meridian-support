import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import get_product as gp


def test_sanitize_sku_normalizes():
    assert gp.sanitize_sku("  com-0001 ") == "COM-0001"


def test_sanitize_sku_rejects_bad_shape():
    with pytest.raises(gp.GetProductValidationError):
        gp.sanitize_sku("COM-01")
    with pytest.raises(gp.GetProductValidationError):
        gp.sanitize_sku("TOOLONGSKU-0001")


def test_sanitize_rejects_non_string():
    with pytest.raises(gp.GetProductValidationError):
        gp.sanitize_sku(123)  # type: ignore[arg-type]


def test_fetch_success(monkeypatch):
    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "get_product"
        assert arguments == {"sku": "MON-0080"}
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Product MON-0080\nPrice: $1")],
        )

    monkeypatch.setattr(gp, "call_tool_sync_guarded", fake_guarded)
    out = gp.fetch_get_product(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c", sku="mon-0080")
    assert "MON-0080" in out


def test_fetch_not_found(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool get_product: Product not found")],
        )

    monkeypatch.setattr(gp, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(gp.GetProductNotFoundError):
        gp.fetch_get_product(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c", sku="COM-9999")


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(gp, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        gp.fetch_get_product(acting_customer_id="", sku="COM-0001")


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(gp, "call_tool_sync_guarded", fake_guarded)
    gp.fetch_get_product(acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c", sku="COM-0001")
    assert any("get_product.success" in r.message for r in caplog.records)
