import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import list_orders as lo
from meridian.tools.get_customer import GetCustomerAccessError

CID = "004cebc1-65d8-4190-99fb-30245b11bc1c"
OTHER = "22222222-2222-2222-2222-222222222222"


def test_sanitize_status():
    assert lo.sanitize_status("  FULFILLED ") == "fulfilled"
    assert lo.sanitize_status(None) is None
    assert lo.sanitize_status("") is None


def test_sanitize_status_invalid():
    with pytest.raises(lo.ListOrdersValidationError):
        lo.sanitize_status("shipped")
    with pytest.raises(lo.ListOrdersValidationError):
        lo.sanitize_status(1)  # type: ignore[arg-type]


def test_build_args_scoped():
    args = lo._build_arguments(acting_customer_id=CID, customer_id=None, status="draft")
    assert args == {"customer_id": CID.lower(), "status": "draft"}


def test_build_args_rejects_other_customer():
    with pytest.raises(GetCustomerAccessError):
        lo._build_arguments(acting_customer_id=CID, customer_id=OTHER, status=None)


def test_fetch_success(monkeypatch):
    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "list_orders"
        assert arguments["customer_id"] == CID.lower()
        assert "status" not in arguments
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="Order A")])

    monkeypatch.setattr(lo, "call_tool_sync_guarded", fake_guarded)
    assert "Order A" in lo.fetch_list_orders(acting_customer_id=CID)


def test_fetch_mcp_error(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool list_orders: x")],
        )

    monkeypatch.setattr(lo, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(lo.ListOrdersMCPError):
        lo.fetch_list_orders(acting_customer_id=CID)


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(lo, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        lo.fetch_list_orders(acting_customer_id=CID)


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(lo, "call_tool_sync_guarded", fake_guarded)
    lo.fetch_list_orders(acting_customer_id=CID, status="submitted")
    assert any("list_orders.success" in r.message for r in caplog.records)
