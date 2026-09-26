"""Central configuration. All values can be overridden via environment or .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "PayPilot AI"
    database_url: str = "sqlite:///./paypilot.db"

    llm_provider: str = "none"  # anthropic | openai | none
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    auto_execute: bool = False
    max_discount_percent: int = 20
    max_actions_per_customer_per_week: int = 2

    @property
    def razorpay_live(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
