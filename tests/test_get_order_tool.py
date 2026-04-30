import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import get_order as go

ACT = "004cebc1-65d8-4190-99fb-30245b11bc1c"
OTHER = "11111111-1111-1111-1111-111111111111"
ORDER = "bf53e8aa-f6ff-4a72-ae36-8c2f49d8c864"


def test_sanitize_order_id():
    assert go.sanitize_order_id(f" {ORDER.upper()} ") == ORDER.lower()


def test_sanitize_rejects_bad():
    with pytest.raises(go.GetOrderValidationError):
        go.sanitize_order_id("nope")


def test_extract_owner():
    body = f"Order ID: x\nCustomer ID: {ACT}\nStatus: ok\n"
    assert go.extract_order_owner_customer_id(body) == ACT.lower()


def test_assert_order_owned_ok():
    go.assert_order_owned_by_actor(f"Customer ID: {ACT}\n", ACT)


def test_assert_order_owned_wrong():
    with pytest.raises(go.GetOrderAccessError):
        go.assert_order_owned_by_actor(f"Customer ID: {OTHER}\n", ACT)


def test_assert_order_owned_missing_line():
    with pytest.raises(go.GetOrderAccessError):
        go.assert_order_owned_by_actor("no customer line", ACT)


def test_fetch_success(monkeypatch):
    body = f"Order ID: {ORDER}\nCustomer ID: {ACT}\nItems:\n"

    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "get_order"
        assert arguments == {"order_id": ORDER.lower()}
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=body)])

    monkeypatch.setattr(go, "call_tool_sync_guarded", fake_guarded)
    out = go.fetch_get_order(acting_customer_id=ACT, order_id=ORDER)
    assert ORDER.lower() in out.lower()


def test_fetch_blocks_cross_tenant(monkeypatch):
    body = f"Order ID: {ORDER}\nCustomer ID: {OTHER}\n"

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=body)])

    monkeypatch.setattr(go, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(go.GetOrderAccessError):
        go.fetch_get_order(acting_customer_id=ACT, order_id=ORDER)


def test_fetch_not_found(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool get_order: Order not found")],
        )

    monkeypatch.setattr(go, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(go.GetOrderNotFoundError):
        go.fetch_get_order(acting_customer_id=ACT, order_id=ORDER)


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(go, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        go.fetch_get_order(acting_customer_id=ACT, order_id=ORDER)


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    body = f"Customer ID: {ACT}\nok"

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=body)])

    monkeypatch.setattr(go, "call_tool_sync_guarded", fake_guarded)
    go.fetch_get_order(acting_customer_id=ACT, order_id=ORDER)
    assert any("get_order.success" in r.message for r in caplog.records)
