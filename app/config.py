from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/govspend",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    refresh_hour: int = 6
    refresh_minute: int = 0
    sync_on_startup: bool = False
    cleanup_hour: int = 2
    cleanup_minute: int = 0

    usaspending_base_url: str = "https://api.usaspending.gov"
    min_award_amount: float = 50_000
    expiration_days: int = 60
    naics_codes: list[str] = [
        "561720",
        "561730",
        "115310",
        "561990",
        "238910",
        "562111",
        "488490",
        "562998",
    ]
    api_page_limit: int = 100
    api_request_delay_seconds: float = 0.25
    max_pages_per_sync: int = 500
    naics_parallel_limit: int = 8


settings = Settings()
