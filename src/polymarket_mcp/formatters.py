"""Compact, JSON-friendly representations of public Polymarket data."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    if number == number.to_integral_value():
        return int(number)
    converted = float(number)
    return converted if converted not in (float("inf"), float("-inf")) else None


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def format_orderbook_levels(levels: list[Any]) -> list[dict[str, int | float | None]]:
    return [{"price": _number(level.price), "size": _number(level.size)} for level in levels]


def format_price_history_point(point: Any) -> dict[str, Any]:
    return {
        "timestamp": _isoformat(point.timestamp),
        "price": _number(point.price),
        "resolution_seconds": point.resolution_seconds,
    }


def format_market(market: Any, *, details: bool = False) -> dict[str, Any]:
    """Return a compact market summary using fields from the SDK Market model."""
    state = market.state
    outcomes = market.outcomes
    result: dict[str, Any] = {
        "id": str(market.id),
        "slug": market.slug,
        "question": market.question,
        "active": state.active,
        "closed": state.closed,
        "yes_price": _number(outcomes.yes.price),
        "no_price": _number(outcomes.no.price),
        "volume": _number(market.metrics.volume),
        "liquidity": _number(market.metrics.liquidity),
    }
    if details:
        result.update(
            {
                "condition_id": str(market.condition_id) if market.condition_id else None,
                "description": market.description,
                "resolved": (market.resolution.resolution_status == "resolved"
                             if market.resolution.resolution_status is not None
                             else False if state.active is True or state.closed is False else None),
                "tick_size": _number(market.trading.minimum_tick_size),
                "neg_risk": state.neg_risk,
                "category": market.category,
                "end_date": _isoformat(state.end_date),
                "best_bid": _number(market.prices.best_bid),
                "best_ask": _number(market.prices.best_ask),
                "spread": _number(market.prices.spread),
                "outcomes": [
                    {
                        "name": outcomes.yes.label,
                        "token_id": str(outcomes.yes.token_id)
                        if outcomes.yes.token_id
                        else None,
                        "price": _number(outcomes.yes.price),
                    },
                    {
                        "name": outcomes.no.label,
                        "token_id": str(outcomes.no.token_id)
                        if outcomes.no.token_id
                        else None,
                        "price": _number(outcomes.no.price),
                    },
                ],
            }
        )
    return result


def format_event(event: Any, markets: list[Any]) -> dict[str, Any]:
    return {
        "event_title": event.title,
        "slug": event.slug,
        "end_date": _isoformat(event.schedule.end_date),
        "volume_24h": _number(event.metrics.volume_24hr),
        "liquidity": _number(event.metrics.liquidity),
        "markets": [
            {
                "id": str(market.id),
                "slug": market.slug,
                "question": market.question,
                "outcomes": [market.outcomes.yes.label, market.outcomes.no.label],
                "outcome_prices": [_number(market.outcomes.yes.price), _number(market.outcomes.no.price)],
                "clob_token_ids": [
                    str(outcome.token_id) if outcome.token_id is not None else None
                    for outcome in (market.outcomes.yes, market.outcomes.no)
                ],
            }
            for market in markets
        ],
    }


def format_orderbook_analysis(
    *,
    token_id: str,
    bids: list[dict[str, Any]],
    asks: list[dict[str, Any]],
    best_bid: Decimal | None,
    best_ask: Decimal | None,
    spread: Decimal | None,
    spread_pct: Decimal | None,
    bid_liquidity_usd: Decimal,
    ask_liquidity_usd: Decimal,
    max_spread_pct: Decimal,
    min_book_liquidity_usd: Decimal,
) -> dict[str, Any]:
    liquidity = bid_liquidity_usd + ask_liquidity_usd
    spread_ok = spread_pct is not None and 0 <= spread_pct <= max_spread_pct
    liquidity_ok = min(bid_liquidity_usd, ask_liquidity_usd) >= min_book_liquidity_usd
    return {
        "token_id": token_id,
        "best_bid": _number(best_bid),
        "best_ask": _number(best_ask),
        "midpoint": _number((best_bid + best_ask) / 2) if best_bid is not None and best_ask is not None else None,
        "spread_abs": _number(spread),
        "spread": _number(spread),
        "spread_pct": _number(spread_pct),
        "bid_liquidity_usd": _number(bid_liquidity_usd),
        "ask_liquidity_usd": _number(ask_liquidity_usd),
        "book_liquidity_usd": _number(liquidity),
        "spread_within_limit": spread_ok,
        "liquidity_meets_minimum": liquidity_ok,
        "acceptable": spread_ok and liquidity_ok,
        "passes_safety_checks": spread_ok and liquidity_ok,
        "bids": bids,
        "asks": asks,
    }
