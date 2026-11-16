from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from polymarket_mcp import client
from polymarket_mcp.config import Settings


@pytest.mark.asyncio
async def test_undeployed_deposit_wallet_never_creates_secure_client(monkeypatch):
    settings = Settings(
        _env_file=None, polymarket_private_key=SecretStr("0x" + "01" * 32),
        polymarket_funder_address="0x" + "02" * 20,
    )
    instance = client.Clients(settings)
    monkeypatch.setattr(client, "classify_account", lambda **kwargs: SimpleNamespace(wallet_type="DEPOSIT_WALLET"))
    transport = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(client, "AsyncTransport", lambda **kwargs: transport)
    monkeypatch.setattr(client, "fetch_deployed", AsyncMock(return_value=False))
    create = AsyncMock()
    monkeypatch.setattr(client.AsyncSecureClient, "create", create)
    try:
        with pytest.raises(client.TradingUnavailableError, match="não existente"):
            await instance.secure()
        create.assert_not_awaited()
        transport.close.assert_awaited_once()
    finally:
        await instance.close()
