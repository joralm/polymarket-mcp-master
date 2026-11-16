from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from polymarket_mcp import server
from polymarket_mcp.config import Settings
from polymarket_mcp.safety import Decision


@pytest.mark.asyncio
async def test_exact_tool_set_and_read_only_error(monkeypatch):
    tools = await server.mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "search_markets", "get_market_details", "analyze_orderbook", "get_price_history",
        "get_portfolio_summary", "get_open_orders", "get_redeemable_positions",
        "place_limit_order", "place_market_order", "cancel_orders",
    }
    assert all(
        tool.annotations and tool.annotations.destructiveHint
        for tool in tools if tool.name in {"place_limit_order", "place_market_order", "cancel_orders"}
    )
    monkeypatch.setattr(server, "settings", Settings(_env_file=None, polymarket_private_key=None))
    result = await server.place_limit_order("token", "BUY", 0.5, 10)
    assert result["code"] == "TRADING_UNAVAILABLE"


@pytest.mark.asyncio
async def test_dry_run_never_submits_and_live_fok_has_price_bound(monkeypatch):
    config = Settings(_env_file=None, polymarket_private_key=SecretStr("key"), dry_run=True)
    monkeypatch.setattr(server, "settings", config)
    secure = SimpleNamespace(place_market_order=AsyncMock(), place_limit_order=AsyncMock())
    monkeypatch.setattr(server, "clients", SimpleNamespace(secure=AsyncMock(return_value=secure), public=object()))
    decision = Decision(
        "token", "BUY", Decimal("10"), Decimal("0.52"), Decimal("20"),
        Decimal("0.01"), Decimal("4"), Decimal("1000"), Decimal("0"), Decimal("2")
    )
    monkeypatch.setattr(server.SafetyGate, "validate", AsyncMock(return_value=decision))
    result = await server.place_market_order("token", "BUY", 10)
    assert result["status"] == "DRY_RUN_SIMULATED"
    secure.place_market_order.assert_not_awaited()
    config.dry_run = False
    secure.place_market_order.return_value = SimpleNamespace(model_dump=lambda **kw: {"ok": True})
    assert (await server.place_market_order("token", "BUY", 10))["ok"]
    secure.place_market_order.assert_awaited_once_with(
        asset_id="token", side="BUY", amount=Decimal("10"),
        max_price=Decimal("0.52"), order_type="FOK",
    )


@pytest.mark.asyncio
async def test_cancel_all_remains_available_in_dry_run(monkeypatch):
    secure = SimpleNamespace(
        cancel_all=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kw: {"canceled": ["1"]})),
        cancel_orders=AsyncMock(),
    )
    monkeypatch.setattr(server, "clients", SimpleNamespace(secure=AsyncMock(return_value=secure)))
    assert await server.cancel_orders([]) == {"canceled": ["1"]}
    secure.cancel_all.assert_awaited_once()
    secure.cancel_orders.assert_not_awaited()


def test_log_redacts_private_key():
    assert server.sanitize({"query": "0x" + "a" * 64}) == {"query": "[redacted]"}


def test_streamable_http_initialization_and_tools():
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(server.mcp.streamable_http_app()) as client:
        response = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        })
        assert response.status_code == 200
        session_id = response.headers["mcp-session-id"]
        response = client.post("/mcp", headers={**headers, "mcp-session-id": session_id}, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
        })
        assert response.status_code == 200
        assert "place_market_order" in response.text
