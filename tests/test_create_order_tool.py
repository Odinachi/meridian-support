import logging
from types import SimpleNamespace

import pytest

from meridian.mcp_guard import MCPAuthRequired
from meridian.tools import create_order as co
from meridian.tools.get_customer import GetCustomerAccessError

CID = "004cebc1-65d8-4190-99fb-30245b11bc1c"
OTHER = "11111111-1111-1111-1111-111111111111"


def test_parse_order_submit_payload():
    text = 'ORDER_SUBMIT: [{"sku":"MON-0080","quantity":1,"unit_price":"10.00"}]'
    got = co.parse_order_submit_payload(text)
    assert got is not None and got[0]["sku"] == "MON-0080"


def test_parse_order_submit_payload_invalid_json():
    assert co.parse_order_submit_payload("ORDER_SUBMIT: [notjson") is None


def test_sanitize_items_ok():
    raw = [{"sku": "com-0001", "quantity": 2, "unit_price": "9.99", "currency": "usd"}]
    out = co.sanitize_order_items(raw)
    assert out == [
        {"sku": "COM-0001", "quantity": 2, "unit_price": "9.99", "currency": "USD"},
    ]


def test_sanitize_items_default_currency():
    out = co.sanitize_order_items([{"sku": "MON-0080", "quantity": 1, "unit_price": "1"}])
    assert out[0]["currency"] == "USD"


def test_sanitize_rejects_bool_quantity():
    with pytest.raises(co.CreateOrderValidationError):
        co.sanitize_order_items([{"sku": "MON-0080", "quantity": True, "unit_price": "1"}])


def test_fetch_success(monkeypatch):
    def fake_guarded(name, arguments, acting_customer_id=None):
        assert name == "create_order"
        assert arguments["customer_id"] == CID.lower()
        assert len(arguments["items"]) == 1
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="Order placed")])

    monkeypatch.setattr(co, "call_tool_sync_guarded", fake_guarded)
    out = co.fetch_create_order(
        acting_customer_id=CID,
        items=[{"sku": "MON-0080", "quantity": 1, "unit_price": "10.00"}],
    )
    assert "placed" in out.lower()


def test_fetch_wrong_customer_id(monkeypatch):
    monkeypatch.setattr(co, "call_tool_sync_guarded", lambda *a, **k: 1 / 0)
    with pytest.raises(GetCustomerAccessError):
        co.fetch_create_order(
            acting_customer_id=CID,
            customer_id=OTHER,
            items=[{"sku": "MON-0080", "quantity": 1, "unit_price": "1"}],
        )


def test_fetch_insufficient_inventory(monkeypatch):
    def fake_guarded(*_a, **_k):
        return SimpleNamespace(
            isError=True,
            content=[
                SimpleNamespace(
                    text="Error executing tool create_order: InsufficientInventoryError: x"
                )
            ],
        )

    monkeypatch.setattr(co, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(co.CreateOrderInsufficientInventoryError):
        co.fetch_create_order(
            acting_customer_id=CID,
            items=[{"sku": "MON-0080", "quantity": 50, "unit_price": "1"}],
        )


def test_fetch_auth_blocked(monkeypatch):
    def fake_guarded(*_a, **_k):
        raise MCPAuthRequired("no")

    monkeypatch.setattr(co, "call_tool_sync_guarded", fake_guarded)
    with pytest.raises(MCPAuthRequired):
        co.fetch_create_order(
            acting_customer_id=CID,
            items=[{"sku": "MON-0080", "quantity": 1, "unit_price": "1"}],
        )


def test_logging_smoke(monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def fake_guarded(*_a, **_k):
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(co, "call_tool_sync_guarded", fake_guarded)
    co.fetch_create_order(
        acting_customer_id=CID,
        items=[{"sku": "MON-0080", "quantity": 1, "unit_price": "1"}],
    )
    assert any("create_order.success" in r.message for r in caplog.records)
