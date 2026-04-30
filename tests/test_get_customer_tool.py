import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import get_customer as gc

CID = "004cebc1-65d8-4190-99fb-30245b11bc1c"
OTHER = "11111111-1111-1111-1111-111111111111"


def test_resolve_defaults_to_session():
    assert gc.resolve_target_customer_id(CID, None) == CID.lower()
    assert gc.resolve_target_customer_id(CID, "  ") == CID.lower()


def test_resolve_accepts_same_id():
    assert gc.resolve_target_customer_id(CID, CID.upper()) == CID.lower()


def test_resolve_rejects_other_customer():
    with pytest.raises(gc.GetCustomerAccessError):
        gc.resolve_target_customer_id(CID, OTHER)


def test_resolve_rejects_bad_uuid():
    with pytest.raises(gc.GetCustomerValidationError):
        gc.resolve_target_customer_id("not-a-uuid", None)


def test_fetch_success(monkeypatch):
    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "get_customer"
        assert arguments == {"customer_id": CID.lower()}
        return SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Customer: Test")],
        )

    monkeypatch.setattr(gc, "call_tool_sync_guarded", fake_guarded)
    out = gc.fetch_get_customer(acting_customer_id=CID)
    assert "Test" in out


def test_fetch_not_found(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool get_customer: Customer not found")],
        )

    monkeypatch.setattr(gc, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(gc.GetCustomerNotFoundError):
        gc.fetch_get_customer(acting_customer_id=CID)


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(gc, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        gc.fetch_get_customer(acting_customer_id=CID)


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(gc, "call_tool_sync_guarded", fake_guarded)
    gc.fetch_get_customer(acting_customer_id=CID)
    assert any("get_customer.success" in r.message for r in caplog.records)
