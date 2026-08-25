from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")
    admin_ids: list[int] = Field(default_factory=list, alias="ADMIN_IDS")
    allowed_user_ids: list[int] = Field(default_factory=list, alias="ALLOWED_USER_IDS")

    market_mode: Literal["demo", "mrkt"] = Field(default="demo", alias="MARKET_MODE")
    database_path: Path = Field(default=Path("data/bot.sqlite3"), alias="DATABASE_PATH")
    rules_path: Path = Field(default=Path("data/combination_rules.json"), alias="RULES_PATH")

    monitor_interval_seconds: int = Field(default=12, ge=5, alias="MONITOR_INTERVAL_SECONDS")
    recent_limit: int = Field(default=40, ge=5, le=100, alias="RECENT_LIMIT")
    comparable_limit: int = Field(default=30, ge=10, le=100, alias="COMPARABLE_LIMIT")
    fast_alerts: bool = Field(default=True, alias="FAST_ALERTS")
    alert_existing_on_start: bool = Field(default=False, alias="ALERT_EXISTING_ON_START")

    sale_fee_percent: float = Field(default=5.0, ge=0, le=30, alias="SALE_FEE_PERCENT")
    base_reserve_percent: float = Field(default=3.0, ge=0, le=30, alias="BASE_RESERVE_PERCENT")
    low_outlier_gap_percent: float = Field(default=18.0, ge=5, le=60, alias="LOW_OUTLIER_GAP_PERCENT")
    high_outlier_multiplier: float = Field(default=2.0, ge=1.2, le=10, alias="HIGH_OUTLIER_MULTIPLIER")

    tg_api_id: int | None = Field(default=None, alias="TG_API_ID")
    tg_api_hash: str | None = Field(default=None, alias="TG_API_HASH")
    tg_phone_number: str | None = Field(default=None, alias="TG_PHONE_NUMBER")
    mrkt_session_name: str = Field(default="mrkt_session", alias="MRKT_SESSION_NAME")
    mrkt_session_dir: Path = Field(default=Path("data/sessions"), alias="MRKT_SESSION_DIR")
    mrkt_auth_token: str | None = Field(default=None, alias="MRKT_AUTH_TOKEN")
    mrkt_proxy: str | None = Field(default=None, alias="MRKT_PROXY")
    mrkt_impersonate: str = Field(default="chrome124", alias="MRKT_IMPERSONATE")

    payout_wallet: str | None = Field(default=None, alias="PAYOUT_WALLET")
    profit_group_chat_id: int | None = Field(default=None, alias="PROFIT_GROUP_CHAT_ID")
    payout_hold_percent: float = Field(default=30.0, ge=0, le=100, alias="PAYOUT_HOLD_PERCENT")
    payout_min_ton: float = Field(default=0.01, gt=0, alias="PAYOUT_MIN_TON")
    payout_max_ton: float = Field(default=100.0, gt=0, alias="PAYOUT_MAX_TON")
    payout_mode: Literal["dry_run", "command", "tonutils"] = Field(default="dry_run", alias="PAYOUT_MODE")
    ton_transfer_command: str | None = Field(default=None, alias="TON_TRANSFER_COMMAND")
    ton_mnemonic_file: Path | None = Field(default=None, alias="TON_MNEMONIC_FILE")
    ton_network: Literal["mainnet", "testnet"] = Field(default="mainnet", alias="TON_NETWORK")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("profit_group_chat_id", mode="before")
    @classmethod
    def parse_optional_int(cls, value):
        if value in (None, ""):
            return None
        return int(value)

    @field_validator("payout_wallet", "tg_phone_number", mode="before")
    @classmethod
    def parse_optional_text(cls, value):
        if value in (None, ""):
            return None
        return str(value).strip() or None

    @field_validator("admin_ids", "allowed_user_ids", mode="before")
    @classmethod
    def parse_ids(cls, value):
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return [int(v) for v in value]
        return [int(part.strip()) for part in str(value).split(",") if part.strip()]

    @property
    def is_mrkt_ready(self) -> bool:
        return bool(self.mrkt_auth_token or (self.tg_api_id and self.tg_api_hash))


def load_settings() -> Settings:
    return Settings()
