from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from polymarket_mcp.config import Settings
from polymarket_mcp.safety import SafetyError, SafetyGate


class Pages:
    def __init__(self, items):
        self.items = items

    async def iter_items(self):
        for item in self.items:
            yield item

    async def __aiter__(self):
        yield SimpleNamespace(items=self.items, limit_reached=False)


def level(price, size):
    return SimpleNamespace(price=Decimal(price), size=Decimal(size))


def setup_gate(**overrides):
    settings = Settings(
        _env_file=None, polymarket_private_key=SecretStr("test"),
        min_book_liquidity_usd=1, **overrides
    )
    book = SimpleNamespace(
        condition_id="market", tick_size=Decimal("0.01"),
        bids=[level("0.49", "1000")], asks=[level("0.51", "1000")]
    )
    public = SimpleNamespace(get_order_book=AsyncMock(return_value=book))
    secure = SimpleNamespace(
        wallet_type="EOA",
        list_positions=lambda **kw: Pages([]),
        list_open_orders=lambda **kw: Pages([]),
    )
    return SafetyGate(settings, public), book, secure


@pytest.mark.asyncio
async def test_rounding_and_exposure_include_positions_and_orders():
    gate, _, secure = setup_gate()
    secure.list_positions = lambda **kw: Pages([SimpleNamespace(current_value="10", total_cost_usdc="12")])
    secure.list_open_orders = lambda **kw: Pages([
        SimpleNamespace(original_size="20", size_matched="10", price="0.5")
    ])
    buy = await gate.validate(secure, "token", "BUY", 20, 0.506)
    sell = await gate.validate(secure, "token", "SELL", 20, 0.506)
    assert buy.price == Decimal("0.50") and buy.shares == 40
    assert sell.price == Decimal("0.51")
    assert buy.exposure == 17
    secure.list_positions = lambda **kw: Pages([SimpleNamespace(current_value="90", total_cost_usdc="90")])
    with pytest.raises(SafetyError, match="Exposição"):
        await gate.validate(secure, "token", "BUY", 20, 0.5)


@pytest.mark.asyncio
async def test_liquidity_spread_and_fok_slippage_fail_closed():
    gate, book, secure = setup_gate()
    book.asks = [level("0.70", "1000")]
    with pytest.raises(SafetyError, match="Spread"):
        await gate.validate(secure, "token", "BUY", 10)
    book.asks = [level("0.51", "1")]
    with pytest.raises(SafetyError, match="Liquidez"):
        await gate.validate(secure, "token", "BUY", 10)
    book.asks = [level("0.51", "1"), level("0.60", "1000")]
    with pytest.raises(SafetyError, match="Slippage"):
        await gate.validate(secure, "token", "BUY", 10)
    book.asks = []
    with pytest.raises(SafetyError, match="Livro vazio"):
        await gate.validate(secure, "token", "BUY", 10)


@pytest.mark.asyncio
async def test_market_order_estimates_shares_and_limit():
    gate, book, secure = setup_gate()
    book.asks = [level("0.51", "10"), level("0.52", "1000")]
    decision = await gate.validate(secure, "token", "BUY", 10)
    assert decision.price == Decimal("0.52")
    assert decision.shares == Decimal("10") + Decimal("4.90") / Decimal("0.52")
    assert decision.slippage_pct > 0
    book.bids = [level("0.49", "10"), level("0.48", "1000")]
    sell = await gate.validate(secure, "token", "SELL", 10)
    assert sell.shares == Decimal("10") / Decimal("0.49")
    assert sell.price == Decimal("0.48")


@pytest.mark.asyncio
async def test_invalid_inputs_and_failed_upstream_block():
    gate, _, secure = setup_gate()
    for amount in (float("nan"), -1, 26):
        with pytest.raises(SafetyError):
            await gate.validate(secure, "token", "BUY", amount, 0.5)
    gate.public.get_order_book.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        await gate.validate(secure, "token", "BUY", 1, 0.5)


@pytest.mark.asyncio
async def test_pagination_truncation_blocks_and_deposit_orders_are_wallet_wide():
    gate, _, secure = setup_gate()
    secure.wallet_type = "DEPOSIT_WALLET"
    secure.list_wallet_open_orders = lambda **kw: Pages()
    class Truncated:
        async def __aiter__(self):
            yield SimpleNamespace(items=[], limit_reached=True)
    secure.list_positions = lambda **kw: Truncated()
    with pytest.raises(SafetyError, match="Paginação incompleta"):
        await gate.validate(secure, "token", "BUY", 1, 0.5)
    secure.list_positions = lambda **kw: Pages([])
    secure.list_wallet_open_orders = lambda **kw: Pages([
        SimpleNamespace(original_size="200", size_matched="0", price="0.5")
    ])
    with pytest.raises(SafetyError, match="Exposição"):
        await gate.validate(secure, "token", "BUY", 1, 0.5)
