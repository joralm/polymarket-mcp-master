from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Literal

import structlog
from polymarket import AsyncPublicClient, AsyncSecureClient

from .config import Settings

log = structlog.get_logger(__name__)
Side = Literal["BUY", "SELL"]


class SafetyError(Exception):
    pass


def number(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise SafetyError(f"{name} inválido") from exc
    if not result.is_finite():
        raise SafetyError(f"{name} deve ser finito")
    return result


@dataclass(frozen=True)
class Decision:
    token_id: str
    side: Side
    amount: Decimal
    price: Decimal
    shares: Decimal
    tick: Decimal
    spread_pct: Decimal
    liquidity: Decimal
    exposure: Decimal
    slippage_pct: Decimal | None

    def as_dict(self) -> dict:
        return {
            "token_id": self.token_id, "side": self.side,
            "amount_usd": str(self.amount), "price": str(self.price),
            "shares": str(self.shares), "tick_size": str(self.tick),
            "spread_pct": str(self.spread_pct), "liquidity_usd": str(self.liquidity),
            "current_exposure_usd": str(self.exposure),
            "slippage_pct": str(self.slippage_pct) if self.slippage_pct is not None else None,
        }


class SafetyGate:
    def __init__(self, settings: Settings, public: AsyncPublicClient):
        self.settings = settings
        self.public = public

    async def validate(
        self, secure: AsyncSecureClient, token_id: str, side: str,
        amount_usd: float, price: float | None = None,
    ) -> Decision:
        try:
            decision = await self._validate(secure, token_id, side, amount_usd, price)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, SafetyError) else f"Falha ao obter dados de risco ({type(exc).__name__})"
            log.warning("safety_decision", approved=False, reason=reason, token_id=token_id, side=side)
            raise
        log.info("safety_decision", approved=True, reason="Todas as verificações passaram", **decision.as_dict())
        return decision

    async def _validate(
        self, secure: AsyncSecureClient, token_id: str, side: str,
        amount_usd: float, price: float | None,
    ) -> Decision:
        if self.settings.polymarket_private_key is None:
            raise SafetyError("Trading indisponível: chave privada não configurada")
        if not token_id or side not in ("BUY", "SELL"):
            raise SafetyError("token_id e side (BUY ou SELL) são obrigatórios")
        amount = number(amount_usd, "amount_usd")
        if amount <= 0 or amount > number(self.settings.max_order_usd, "max_order_usd"):
            raise SafetyError("amount_usd deve ser positivo e não exceder MAX_ORDER_USD")
        book = await self.public.get_order_book(asset_id=token_id)
        tick = number(book.tick_size, "tick_size")
        if not 0 < tick < 1:
            raise SafetyError("tick_size inválido")
        bids = sorted(((number(x.price, "bid"), number(x.size, "size")) for x in book.bids), reverse=True)
        asks = sorted((number(x.price, "ask"), number(x.size, "size")) for x in book.asks)
        if not bids or not asks or any(not 0 < p < 1 or s <= 0 for p, s in bids + asks):
            raise SafetyError("Livro vazio ou contém níveis inválidos")
        best_bid, best_ask = bids[0][0], asks[0][0]
        if best_ask < best_bid:
            raise SafetyError("Livro cruzado")
        midpoint = (best_bid + best_ask) / 2
        spread = (best_ask - best_bid) / midpoint * 100
        if spread > number(self.settings.max_spread_pct, "max_spread_pct"):
            raise SafetyError(f"Spread {spread}% excede MAX_SPREAD_PCT")
        levels = asks if side == "BUY" else bids
        liquidity = sum((p * s for p, s in levels[:5]), Decimal(0))
        if liquidity < number(self.settings.min_book_liquidity_usd, "min_book_liquidity_usd"):
            raise SafetyError(f"Liquidez {liquidity} inferior a MIN_BOOK_LIQUIDITY_USD")
        condition_id = str(book.condition_id)
        if not condition_id:
            raise SafetyError("Livro sem condition_id")
        positions = await self._all_items(secure.list_positions(
            condition_id=condition_id, status="OPEN", full_history=True,
            filter_amount=0, include_archived=True,
        ))
        order_pages = (secure.list_wallet_open_orders(market=condition_id)
                       if secure.wallet_type == "DEPOSIT_WALLET"
                       else secure.list_open_orders(market=condition_id))
        orders = await self._all_items(order_pages)
        exposure = Decimal(0)
        for position in positions:
            value = number(position.current_value, "current_value")
            cost = number(position.total_cost_usdc, "total_cost_usdc")
            if value < 0 or cost < 0:
                raise SafetyError("Posição contém valor ou custo negativo")
            exposure += max(value, cost)
        for order in orders:
            remaining = number(order.original_size, "original_size") - number(order.size_matched, "size_matched")
            order_price = number(order.price, "order_price")
            if remaining < 0 or not 0 < order_price < 1:
                raise SafetyError("Ordem pendente contém valores inválidos")
            exposure += remaining * order_price
        if exposure + amount > number(self.settings.max_market_exposure_usd, "max_market_exposure_usd"):
            raise SafetyError(f"Exposição projetada {exposure + amount} excede MAX_MARKET_EXPOSURE_USD")
        slippage = None
        if price is None:
            best = levels[0][0]
            remaining = amount if side == "BUY" else amount / best
            shares = Decimal(0)
            worst = best
            for level_price, size in levels:
                worst = level_price
                available = level_price * size if side == "BUY" else size
                taken = min(remaining, available)
                shares += taken / level_price if side == "BUY" else taken
                if taken == remaining:
                    remaining = Decimal(0)
                    break
                remaining -= taken
            if remaining > 0:
                raise SafetyError("Liquidez insuficiente para executar integralmente a ordem FOK")
            slippage = abs(worst - best) / best * 100
            if slippage > number(self.settings.max_spread_pct, "max_spread_pct"):
                raise SafetyError(f"Slippage {slippage}% excede MAX_SPREAD_PCT")
            order_price = worst
        else:
            requested = number(price, "price")
            if not 0 < requested < 1:
                raise SafetyError("price deve estar entre 0 e 1")
            rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
            order_price = (requested / tick).to_integral_value(rounding=rounding) * tick
            if not 0 < order_price < 1:
                raise SafetyError("price inválido após arredondamento ao tick")
            shares = amount / order_price
        return Decision(token_id, side, amount, order_price, shares, tick, spread, liquidity, exposure, slippage)

    @staticmethod
    async def _all_items(paginator):
        items = []
        async for page in paginator:
            if page.limit_reached:
                raise SafetyError("Paginação incompleta; não é possível determinar a exposição")
            items.extend(page.items)
        return items
