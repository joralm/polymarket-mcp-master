from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from polymarket_mcp.public import analyze_orderbook, search_markets


class Items:
    def __init__(self, items):
        self.items = items

    async def iter_items(self):
        for item in self.items:
            yield item


@pytest.mark.asyncio
async def test_search_returns_compact_events_with_active_markets():
    yes = SimpleNamespace(label="Yes", price=Decimal("0.4"), token_id="1")
    no = SimpleNamespace(label="No", price=Decimal("0.6"), token_id="2")
    market = SimpleNamespace(
        id="123", slug="question", question="Question?",
        outcomes=SimpleNamespace(yes=yes, no=no),
        state=SimpleNamespace(active=True, closed=False),
    )
    event = SimpleNamespace(
        id="event", title="Event", slug="event", markets=[market],
        schedule=SimpleNamespace(end_date=None),
        metrics=SimpleNamespace(volume_24hr=Decimal("100"), liquidity=Decimal("50")),
    )
    client = SimpleNamespace(search=lambda **kwargs: Items([SimpleNamespace(events=[event])]))
    result = await search_markets(client, "question")
    assert result[0]["event_title"] == "Event"
    assert result[0]["volume_24h"] == 100
    assert result[0]["markets"][0]["clob_token_ids"] == ["1", "2"]


@pytest.mark.asyncio
async def test_orderbook_safety_requires_liquidity_on_both_sides():
    def level(price, size):
        return SimpleNamespace(price=Decimal(price), size=Decimal(size))

    book = SimpleNamespace(
        bids=[level("0.49", "10"), level("0.48", "1000")],
        asks=[level("0.52", "1000"), level("0.51", "1000")],
    )
    client = SimpleNamespace(get_order_book=AsyncMock(return_value=book))
    result = await analyze_orderbook(client, "token", depth=1, min_book_liquidity_usd=100)
    assert result["best_bid"] == 0.49 and result["best_ask"] == 0.51
    assert result["midpoint"] == 0.5
    assert result["passes_safety_checks"] is False
