"""Portfolio queries for polymarket-client 0.20.0.

Amounts are decimal strings, sizes are shares, and PnL/value fields are USD.
The summary covers the Data API's open book, including unredeemed positions;
its PnL is not lifetime account PnL. Concentration is by condition and uses
the open book's marked value, excluding cash. A zero-value book has undefined
concentration percentages, represented by None.

The Data API excludes inactive markets even with include_archived=True. Its
snapshot and the CLOB balance are obtained separately and can change between
requests. Combo positions are not included.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polymarket import AsyncSecureClient


async def _all_items(paginator: Any) -> list[Any]:
    items = []
    async for page in paginator:
        if page.limit_reached:
            raise RuntimeError("Pagination depth limit reached; results may be incomplete")
        items.extend(page.items)
    return items


def _position(position: Any) -> dict[str, Any]:
    return {
        "condition_id": position.condition_id,
        "asset_id": position.asset_id,
        "title": position.title,
        "market": position.title,
        "outcome": position.outcome,
        "outcome_index": position.outcome_index,
        "size": str(position.current_size),
        "avg_price": str(position.avg_price),
        "current_price": str(position.current_price),
        "current_value": str(position.current_value),
        "realized_pnl": str(position.realized_pnl),
        "unrealized_pnl": str(position.unrealized_pnl),
        "pnl_usd": str(position.unrealized_pnl),
        "total_pnl": str(position.total_pnl),
        "status": str(position.status),
        "redeemable": position.redeemable,
        "negative_risk": position.negative_risk,
    }


async def get_portfolio_summary(client: AsyncSecureClient) -> dict[str, Any]:
    """Return pUSD balance, open-book positions, value, PnL and concentration.

    All PnL totals sum the SDK fields over the current open book only.
    ``total_value`` adds the CLOB collateral balance to the marked book value.
    Values are decimal strings, percentages are from 0 to 100.
    """
    balance = await client.get_balance_allowance(asset_type="COLLATERAL")
    positions = await _all_items(
        client.list_positions(status="OPEN", filter_amount=0, include_archived=True, full_history=True)
    )
    paginator = (client.list_wallet_open_orders() if client.wallet_type == "DEPOSIT_WALLET"
                 else client.list_open_orders())
    orders = await _all_items(paginator)
    cash = Decimal(balance.balance) / Decimal(1_000_000)
    reserved = sum(
        ((o.original_size - o.size_matched) * o.price for o in orders if o.side == "BUY"),
        Decimal(0),
    )
    if reserved < 0:
        raise ValueError("Invalid pending-order collateral")
    value = sum((p.current_value for p in positions), Decimal(0))
    realized = sum((p.realized_pnl for p in positions), Decimal(0))
    unrealized = sum((p.unrealized_pnl for p in positions), Decimal(0))
    pnl = sum((p.total_pnl for p in positions), Decimal(0))

    by_condition: dict[str, Decimal] = {}
    for p in positions:
        by_condition[p.condition_id] = by_condition.get(p.condition_id, Decimal(0)) + p.current_value
    concentration = [
        {
            "condition_id": condition_id,
            "value": str(market_value),
            "percentage": str(market_value * 100 / value) if value else None,
        }
        for condition_id, market_value in sorted(
            by_condition.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "wallet": client.wallet,
        "balance_pusd": str(cash),
        "free_pusd_estimate": str(max(Decimal(0), cash - reserved)),
        "open_buy_notional": str(reserved),
        "positions_value": str(value),
        "total_value": str(cash + value),
        "realized_pnl": str(realized),
        "unrealized_pnl": str(unrealized),
        "total_pnl": str(pnl),
        "positions": [_position(p) for p in positions],
        "concentration": concentration,
        "largest_position_concentration_pct": (
            str(max(p.current_value for p in positions) * 100 / value) if value else None
        ),
    }


async def get_open_orders(
    client: AsyncSecureClient, market_id: str | None = None
) -> list[dict[str, Any]]:
    """List open orders; market_id is a condition ID, not a Gamma market ID.

    Deposit wallets include all owner/session-key orders. Other wallet types
    expose orders belonging to the client's credentials.
    """
    if client.wallet_type == "DEPOSIT_WALLET":
        paginator = client.list_wallet_open_orders(market=market_id)
    else:
        paginator = client.list_open_orders(market=market_id)
    orders = await _all_items(paginator)
    return [
        {
            "id": order.id,
            "order_id": order.id,
            "condition_id": order.condition_id,
            "asset_id": order.asset_id,
            "token_id": order.asset_id,
            "side": str(order.side),
            "outcome": order.outcome,
            "price": str(order.price),
            "original_size": str(order.original_size),
            "size_matched": str(order.size_matched),
            "size_remaining": str(order.original_size - order.size_matched),
            "status": order.status,
            "order_type": order.order_type,
        }
        for order in orders
    ]


async def get_redeemable_positions(client: AsyncSecureClient) -> list[dict[str, Any]]:
    """List winning redeemable positions, excluding zero-payout holdings."""
    positions = await _all_items(
        client.list_positions(
            status="REDEEMABLE", filter_amount=0, include_archived=True
        )
    )
    return [_position(p) for p in positions if p.redeemable and p.current_size > 0 and p.current_value > 0]


__all__ = ["get_portfolio_summary", "get_open_orders", "get_redeemable_positions"]
