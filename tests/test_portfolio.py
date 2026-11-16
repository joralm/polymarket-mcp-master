from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from polymarket_mcp.portfolio import (  # noqa: E402
    get_open_orders,
    get_portfolio_summary,
    get_redeemable_positions,
)


def position(condition, value, *, redeemable=False, size="2"):
    return SimpleNamespace(
        condition_id=condition,
        asset_id="asset-" + condition,
        title=None,
        outcome="Yes",
        outcome_index=0,
        current_size=Decimal(size),
        avg_price=Decimal("0.4"),
        current_price=Decimal("0.6"),
        current_value=Decimal(value),
        realized_pnl=Decimal("0.2"),
        unrealized_pnl=Decimal("0.3"),
        total_pnl=Decimal("0.5"),
        status="REDEEMABLE" if redeemable else "OPEN",
        redeemable=redeemable,
        negative_risk=False,
    )


class Pages:
    def __init__(self, *pages):
        self.pages = pages

    def __aiter__(self):
        async def iterate():
            for page in self.pages:
                if isinstance(page, Exception):
                    raise page
                yield page

        return iterate()


def page(*items, limit_reached=False):
    return SimpleNamespace(items=items, limit_reached=limit_reached)


class Client:
    wallet = "proxy-address"
    wallet_type = "POLY_PROXY"

    def __init__(self):
        self.position_pages = Pages()
        self.order_pages = Pages()
        self.calls = []

    async def get_balance_allowance(self, **kwargs):
        self.calls.append(("balance", kwargs))
        return SimpleNamespace(balance=1_250_000)

    def list_positions(self, **kwargs):
        self.calls.append(("positions", kwargs))
        return self.position_pages

    def list_open_orders(self, **kwargs):
        self.calls.append(("orders", kwargs))
        return self.order_pages

    def list_wallet_open_orders(self, **kwargs):
        self.calls.append(("wallet_orders", kwargs))
        return self.order_pages


class PortfolioTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_reads_all_pages_and_aggregates_conditions(self):
        client = Client()
        client.position_pages = Pages(
            page(position("a", "1.2"), position("b", "0.8")),
            page(position("a", "2", redeemable=True)),
        )
        result = await get_portfolio_summary(client)
        self.assertEqual(client.calls, [
            ("balance", {"asset_type": "COLLATERAL"}),
            ("positions", {"status": "OPEN", "filter_amount": 0, "include_archived": True, "full_history": True}),
            ("orders", {}),
        ])
        self.assertEqual(result["wallet"], "proxy-address")
        self.assertEqual(result["balance_pusd"], "1.25")
        self.assertEqual(result["positions_value"], "4.0")
        self.assertEqual(result["total_value"], "5.25")
        self.assertEqual(result["total_pnl"], "1.5")
        self.assertEqual(result["unrealized_pnl"], "0.9")
        self.assertEqual(len(result["positions"]), 3)
        self.assertEqual(result["concentration"], [
            {"condition_id": "a", "value": "3.2", "percentage": "80"},
            {"condition_id": "b", "value": "0.8", "percentage": "20"},
        ])

    async def test_empty_and_zero_value_books(self):
        client = Client()
        result = await get_portfolio_summary(client)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["positions_value"], "0")
        client.position_pages = Pages(page(position("a", "0", redeemable=True)))
        result = await get_portfolio_summary(client)
        self.assertIsNone(result["concentration"][0]["percentage"])

    async def test_orders_for_deposit_and_proxy_wallets(self):
        client = Client()
        order = SimpleNamespace(
            id="o", condition_id="condition", asset_id="asset", side="BUY",
            outcome="Yes", price=Decimal("0.45"), original_size=Decimal("10.5"),
            size_matched=Decimal("2.25"), status="LIVE", order_type="GTC",
        )
        client.order_pages = Pages(page(), page(order))
        result = await get_open_orders(client, "condition")
        self.assertEqual(result[0]["size_remaining"], "8.25")
        self.assertEqual(client.calls, [("orders", {"market": "condition"})])
        client.wallet_type = "DEPOSIT_WALLET"
        await get_open_orders(client)
        self.assertEqual(client.calls[-1], ("wallet_orders", {"market": None}))

    async def test_redeemable_excludes_losing_positions(self):
        client = Client()
        loser = position("loser", "0", redeemable=True)
        loser.current_price = Decimal("0")
        client.position_pages = Pages(
            page(position("winner", "5", redeemable=True)),
            page(loser),
        )
        result = await get_redeemable_positions(client)
        self.assertEqual([p["condition_id"] for p in result], ["winner"])
        self.assertEqual(client.calls, [("positions", {
            "status": "REDEEMABLE", "filter_amount": 0, "include_archived": True
        })])

    async def test_errors_and_incomplete_pages_are_not_hidden(self):
        client = Client()
        client.position_pages = Pages(page(position("a", "1")), RuntimeError("API error"))
        with self.assertRaisesRegex(RuntimeError, "API error"):
            await get_portfolio_summary(client)
        client.order_pages = Pages(page(limit_reached=True))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            await get_open_orders(client)
        client.order_pages = Pages()
        bad_position = position("bad", "1")
        del bad_position.current_value
        client.position_pages = Pages(page(bad_position))
        with self.assertRaises(AttributeError):
            await get_portfolio_summary(client)


if __name__ == "__main__":
    unittest.main()
