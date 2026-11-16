import asyncio

from eth_account import Account
from polymarket import AsyncPublicClient, AsyncSecureClient
from polymarket._internal.actions.relayer.deployed import fetch_deployed
from polymarket._internal.environment import get_environment_config
from polymarket._internal.wallet import classify_account
from polymarket.clients._transport import AsyncTransport
from polymarket.environments import PRODUCTION
from polymarket.models.clob.relayer import RelayerTransactionType

from .config import Settings


class TradingUnavailableError(Exception):
    pass


class Clients:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.public = AsyncPublicClient()
        self._secure: AsyncSecureClient | None = None
        self._lock = asyncio.Lock()

    async def secure(self) -> AsyncSecureClient:
        key = self.settings.polymarket_private_key
        if key is None:
            raise TradingUnavailableError("Chave privada não configurada; servidor em modo read-only.")
        async with self._lock:
            if self._secure is None:
                # The SDK defaults to deploying a deposit wallet if wallet is omitted.
                wallet = self.settings.polymarket_funder_address or Account.from_key(
                    key.get_secret_value()
                ).address
                signer = Account.from_key(key.get_secret_value())
                config = get_environment_config(PRODUCTION)
                classification = classify_account(
                    signer=signer.address, wallet=wallet, config=config.wallet_derivation
                )
                if classification.wallet_type == "DEPOSIT_WALLET":
                    relayer = AsyncTransport(base_url=config.relayer_url)
                    try:
                        deployed = await fetch_deployed(
                            relayer, address=wallet, type=RelayerTransactionType.WALLET
                        )
                    finally:
                        await relayer.close()
                    if not deployed:
                        raise TradingUnavailableError(
                            "Deposit Wallet não existente; o servidor não cria carteiras automaticamente."
                        )
                self._secure = await AsyncSecureClient.create(
                    private_key=key.get_secret_value(), wallet=wallet
                )
            return self._secure

    async def close(self) -> None:
        if self._secure is not None:
            await self._secure.close()
        await self.public.close()
