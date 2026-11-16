import asyncio
import functools
import inspect
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from polymarket.models.clob.order_response import RejectedOrder

from . import portfolio, public
from .client import Clients, TradingUnavailableError
from .config import Settings
from .logger import configure_logging, sanitize
from .safety import SafetyError, SafetyGate

settings = Settings()
configure_logging(settings)
log = structlog.get_logger(__name__)
clients: Clients | None = None
trade_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastMCP):
    global clients
    clients = Clients(settings)
    log.info(
        "server_started", mode="trading" if settings.polymarket_private_key else "read-only",
        dry_run=settings.dry_run, max_order_usd=settings.max_order_usd,
        max_market_exposure_usd=settings.max_market_exposure_usd,
        max_spread_pct=settings.max_spread_pct,
        min_book_liquidity_usd=settings.min_book_liquidity_usd,
        transport=settings.mcp_transport,
    )
    try:
        yield
    finally:
        await clients.close()
        clients = None


mcp = FastMCP(
    "Polymarket V2", host=settings.mcp_host, port=settings.mcp_port,
    lifespan=lifespan,
)


def current_clients() -> Clients:
    if clients is None:
        raise RuntimeError("Servidor ainda não inicializado")
    return clients


def tool_call(fn: Callable) -> Callable:
    @functools.wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        bound = inspect.signature(fn).bind(*args, **kwargs)
        bound.apply_defaults()
        params = sanitize(bound.arguments)
        try:
            return await fn(*args, **kwargs)
        except (SafetyError, TradingUnavailableError, ValueError) as exc:
            log.warning("tool_error", tool=fn.__name__, error_type=type(exc).__name__, parameters=params)
            code = "TRADING_UNAVAILABLE" if isinstance(exc, TradingUnavailableError) else (
                "SAFETY_BLOCKED" if isinstance(exc, SafetyError) else "INVALID_INPUT"
            )
            # Only locally produced safety messages are safe to reveal.
            message = str(exc) if isinstance(exc, (SafetyError, TradingUnavailableError)) else "Parâmetros inválidos."
            return {"error": message, "code": code}
        except Exception as exc:
            services = {
                "search_markets": "Gamma", "get_market_details": "Gamma",
                "analyze_orderbook": "CLOB", "get_price_history": "Data",
                "get_portfolio_summary": "CLOB/Data", "get_open_orders": "CLOB",
                "get_redeemable_positions": "Data", "place_limit_order": "CLOB/Data",
                "place_market_order": "CLOB/Data", "cancel_orders": "CLOB",
            }
            log.error("polymarket_api_error", tool=fn.__name__, service=services[fn.__name__],
                      error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None),
                      parameters=params)
            return {"error": "Não foi possível concluir a operação. Consulte os logs.", "code": "API_ERROR"}
        finally:
            log.info("tool_called", tool=fn.__name__, parameters=params, duration_ms=round((time.monotonic() - start) * 1000, 2))
    return wrapped


@mcp.tool()
@tool_call
async def search_markets(query: str, limit: int = 5, only_active: bool = True) -> list[dict] | dict:
    """Pesquisa eventos e mercados; devolve apenas dados úteis para análise."""
    return await public.search_markets(current_clients().public, query, limit, only_active)


@mcp.tool()
@tool_call
async def get_market_details(slug_or_id: str) -> dict:
    """Obtém regras de resolução, estado, preços e parâmetros de um mercado."""
    return await public.get_market_details(current_clients().public, slug_or_id)


@mcp.tool()
@tool_call
async def analyze_orderbook(token_id: str, depth: int = 5) -> dict:
    """Analisa spread e liquidez de um token; não garante execução."""
    return await public.analyze_orderbook(current_clients().public, token_id, depth, settings.max_spread_pct, settings.min_book_liquidity_usd)


@mcp.tool()
@tool_call
async def get_price_history(token_id: str, interval: str = "1d") -> dict:
    """Obtém uma série temporal resumida de preços para o intervalo indicado."""
    return await public.get_price_history(current_clients().public, token_id, interval)


@mcp.tool()
@tool_call
async def get_portfolio_summary() -> dict:
    """Resume saldo, posições, PnL não realizado e concentração da carteira."""
    return await portfolio.get_portfolio_summary(await current_clients().secure())


@mcp.tool()
@tool_call
async def get_open_orders(market_id: str | None = None) -> list[dict] | dict:
    """Lista ordens abertas, opcionalmente filtradas pelo condition ID do mercado."""
    return await portfolio.get_open_orders(await current_clients().secure(), market_id)


@mcp.tool()
@tool_call
async def get_redeemable_positions() -> list[dict] | dict:
    """Lista posições vencedoras que podem ser resgatadas; não executa resgates."""
    return await portfolio.get_redeemable_positions(await current_clients().secure())


async def execute_order(token_id: str, side: str, amount: float, price: float | None) -> dict:
    if settings.polymarket_private_key is None:
        log.warning("safety_decision", approved=False, reason="Trading indisponível: chave privada não configurada", token_id=sanitize(token_id), side=sanitize(side))
        raise TradingUnavailableError("Chave privada não configurada; servidor em modo read-only.")
    async with trade_lock:
        c = current_clients()
        try:
            secure = await c.secure()
        except Exception as exc:
            log.warning("safety_decision", approved=False, reason=f"Cliente autenticado indisponível ({type(exc).__name__})", token_id=token_id, side=side)
            raise
        decision = await SafetyGate(settings, c.public).validate(secure, token_id, side, amount, price)
        if settings.dry_run:
            result = {"status": "DRY_RUN_SIMULATED", **decision.as_dict(), "order_type": "GTC" if price is not None else "FOK"}
            log.info("order_simulated", **result)
            return result
        if price is not None:
            response = await secure.place_limit_order(
                asset_id=token_id, side=decision.side, price=decision.price, size=decision.shares
            )
        elif decision.side == "BUY":
            response = await secure.place_market_order(
                asset_id=token_id, side="BUY", amount=decision.amount,
                max_price=decision.price, order_type="FOK"
            )
        else:
            response = await secure.place_market_order(
                asset_id=token_id, side="SELL", shares=decision.shares,
                min_price=decision.price, order_type="FOK"
            )
        if isinstance(response, RejectedOrder):
            return {"error": response.message, "code": response.code}
        return response.model_dump(mode="json")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
@tool_call
async def place_limit_order(token_id: str, side: str, price: float, size_usd: float) -> dict:
    """Simula ou envia ordem GTC em USD após verificações de risco. side é BUY ou SELL."""
    return await execute_order(token_id, side, size_usd, price)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
@tool_call
async def place_market_order(token_id: str, side: str, amount_usd: float) -> dict:
    """Simula ou envia ordem FOK com preço limitado e verificações de risco."""
    return await execute_order(token_id, side, amount_usd, None)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
@tool_call
async def cancel_orders(order_ids: list[str] | None = None) -> dict:
    """Cancela as ordens especificadas; lista vazia ou omitida cancela todas, mesmo em DRY_RUN."""
    async with trade_lock:
        secure = await current_clients().secure()
        result = await (secure.cancel_orders(order_ids=order_ids) if order_ids else secure.cancel_all())
        return result.model_dump(mode="json")


def main() -> None:
    mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
