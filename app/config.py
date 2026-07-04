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
    max_award_amount: float | None = 350_000
    expiration_days: int = 60
    naics_codes: list[str] = [
        "561720",  # Janitorial Services
        "561210",  # Facilities Support Services
        "561730",  # Landscaping Services
        "561710",  # Pest Control Services
        "562111",  # Solid Waste Collection
        "561790",  # Other Services to Buildings and Dwellings
        "561740",  # Carpet and Upholstery Cleaning
        "562119",  # Other Waste Collection
        "561439",  # Document Shredding Services
        "541930",  # Translation and Interpretation Services
        "811192",  # Car Wash and Vehicle Cleaning Services
        "238220",  # Plumbing and HVAC Maintenance Services
        "562910",  # Remediation Services
        "484210",  # Moving and Relocation Services
        "484110",  # General Freight Trucking Local
        "492110",  # Couriers and Messengers
        "711320",  # Photography and Videography Services
        "532490",  # Equipment Rental and Leasing
        "561422",  # Telephone Answering Services
    ]
    api_page_limit: int = 100
    api_request_delay_seconds: float = 0.25
    max_pages_per_sync: int = 500
    naics_parallel_limit: int = 8


settings = Settings()
