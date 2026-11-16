from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mcp_transport: Literal["streamable-http", "stdio"] = "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    polymarket_private_key: SecretStr | None = None
    polymarket_funder_address: str | None = None
    dry_run: bool = True
    max_order_usd: float = Field(default=25.0, gt=0, allow_inf_nan=False)
    max_market_exposure_usd: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    max_spread_pct: float = Field(default=5.0, ge=0, allow_inf_nan=False)
    min_book_liquidity_usd: float = Field(default=500.0, ge=0, allow_inf_nan=False)

    @field_validator("polymarket_private_key", mode="before")
    @classmethod
    def empty_key_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("polymarket_funder_address", mode="before")
    @classmethod
    def empty_wallet_is_none(cls, value: object) -> object:
        return None if value == "" else value
