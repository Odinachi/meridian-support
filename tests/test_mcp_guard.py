import pytest

from meridian.mcp_guard import MCPAuthRequired, call_tool_sync_guarded


def test_verify_allowed_without_customer(monkeypatch):
    from types import SimpleNamespace

    def fake(name, arguments):
        assert name == "verify_customer_pin"
        return SimpleNamespace(isError=False, content=[])

    monkeypatch.setattr("meridian.mcp_guard.call_tool_sync", fake)
    call_tool_sync_guarded(
        "verify_customer_pin",
        {"email": "a@b.co", "pin": "1234"},
        acting_customer_id=None,
    )


def test_list_products_blocked_without_customer(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("MCP should not be called without a session")

    monkeypatch.setattr("meridian.mcp_guard.call_tool_sync", boom)
    with pytest.raises(MCPAuthRequired):
        call_tool_sync_guarded("list_products", {}, acting_customer_id=None)


def test_list_products_allowed_with_customer(monkeypatch):
    from types import SimpleNamespace

    def fake(name, arguments):
        return SimpleNamespace(isError=False, content=[])

    monkeypatch.setattr("meridian.mcp_guard.call_tool_sync", fake)
    call_tool_sync_guarded(
        "list_products",
        {"is_active": True},
        acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c",
    )
