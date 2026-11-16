"""Public-data helpers backed by the official Polymarket async client."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .formatters import (
    format_event,
    format_market,
    format_orderbook_analysis,
    format_orderbook_levels,
    format_price_history_point,
)


def _as_decimal(value: int | float | Decimal, name: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return number


async def search_markets(
    client: Any,
    query: str,
    limit: int = 5,
    only_active: bool = True,
) -> list[dict[str, Any]]:
    """Search public markets and return at most ``limit`` compact summaries."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    paginator = client.search(
        q=query,
        events_status="active" if only_active else None,
        page_size=limit,
    )
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    async for result in paginator.iter_items():
        for event in result.events:
            event_id = str(event.id)
            if event_id in seen:
                continue
            markets = [m for m in event.markets if not only_active or (m.state.active is True and m.state.closed is False)]
            if not markets:
                continue
            seen.add(event_id)
            events.append(format_event(event, markets))
            if len(events) == limit:
                return events
    return events


async def get_market_details(client: Any, slug_or_id: str) -> dict[str, Any]:
    """Fetch one public market by its slug or numeric market ID."""
    identifier = str(slug_or_id)
    if identifier.isdecimal():
        market = await client.get_market(id=identifier)
    else:
        market = await client.get_market(slug=identifier)
    return format_market(market, details=True)


async def analyze_orderbook(
    client: Any,
    token_id: str,
    depth: int = 5,
    max_spread_pct: float = 5.0,
    min_book_liquidity_usd: float = 500.0,
) -> dict[str, Any]:
    """Summarize top-of-book spread and notional liquidity for one token."""
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 100:
        raise ValueError("depth must be between 1 and 100")
    max_spread = _as_decimal(max_spread_pct, "max_spread_pct")
    min_liquidity = _as_decimal(min_book_liquidity_usd, "min_book_liquidity_usd")
    if max_spread < 0:
        raise ValueError("max_spread_pct must be non-negative")
    if min_liquidity < 0:
        raise ValueError("min_book_liquidity_usd must be non-negative")

    book = await client.get_order_book(token_id=token_id)
    best_bids = sorted(book.bids, key=lambda level: level.price, reverse=True)[:depth]
    best_asks = sorted(book.asks, key=lambda level: level.price)[:depth]
    best_bid = best_bids[0].price if best_bids else None
    best_ask = best_asks[0].price if best_asks else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    midpoint = (best_ask + best_bid) / 2 if best_bid is not None and best_ask is not None else None
    spread_pct = spread / midpoint * 100 if spread is not None and midpoint else None

    bid_liquidity = sum((level.price * level.size for level in best_bids), Decimal(0))
    ask_liquidity = sum((level.price * level.size for level in best_asks), Decimal(0))
    return format_orderbook_analysis(
        token_id=token_id,
        bids=format_orderbook_levels(best_bids),
        asks=format_orderbook_levels(best_asks),
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        spread_pct=spread_pct,
        bid_liquidity_usd=bid_liquidity,
        ask_liquidity_usd=ask_liquidity,
        max_spread_pct=max_spread,
        min_book_liquidity_usd=min_liquidity,
    )


async def get_price_history(
    client: Any,
    token_id: str,
    interval: str = "1d",
) -> dict[str, Any]:
    """Return historical price points for a CLOB token using the public API."""
    paginator = client.list_price_history(asset_id=token_id, interval=interval)
    points = [format_price_history_point(point) async for point in paginator.iter_items()]
    if len(points) > 100:
        points = [points[i * (len(points) - 1) // 99] for i in range(100)]
    return {"token_id": token_id, "interval": interval, "points": points}


__all__ = [
    "analyze_orderbook",
    "get_market_details",
    "get_price_history",
    "search_markets",
]
