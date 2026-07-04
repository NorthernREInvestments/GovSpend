from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import ContractStatus, WatchlistPriority, WatchlistStatus


class ContractBase(BaseModel):
    contract_name: str
    award_amount: float
    estimated_annual_value: float = 0
    start_date: date | None = None
    total_obligation: float = 0
    base_exercised_options_value: float | None = None
    base_all_options_value: float | None = None
    pop_flag: str = ""
    period_years: float | None = None
    remaining_option_years: float | None = None
    total_runway_years: float | None = None
    recurring_fit: str = ""
    recurring_fit_score: float = 0
    recurrence_pattern: str = ""
    option_extensions_count: int = 0
    prior_similar_awards_count: int = 0
    agency: str
    place_of_performance: str
    incumbent_name: str
    expiration_date: date
    potential_end_date: date | None = None
    number_of_offers_received: int | None = None
    contracting_office: str
    co_name: str
    status: ContractStatus = ContractStatus.WATCHING


class ContractRead(ContractBase):
    id: int
    award_id: str
    generated_internal_id: str | None = None
    naics_code: str
    set_aside: str = ""
    extent_competed: str = ""
    solicitation_number: str = ""
    pursuit_score: float = 0
    priority_tier_label: str = ""
    expires_in_label: str = ""
    bidders_label: str = ""
    notes: str = ""
    last_synced_at: datetime

    model_config = {"from_attributes": True}


class ContractNotesUpdate(BaseModel):
    notes: str = Field(default="", max_length=5000)


class ContractStatusUpdate(BaseModel):
    status: ContractStatus


class SyncStatusRead(BaseModel):
    last_sync: datetime | None = None
    contracts_found: int = 0
    contracts_upserted: int = 0
    pages_scanned: int = 0
    status: str = "unknown"
    message: str | None = None


class DashboardStats(BaseModel):
    total_contracts: int
    total_value: float
    expiring_this_month: int
    by_status: dict[str, int]


class DashboardLiveRead(BaseModel):
    contracts: list[ContractRead]
    hot_leads: list[ContractRead]
    stats: DashboardStats


class BrowseContractRead(BaseModel):
    award_id: str
    generated_internal_id: str | None = None
    contract_name: str
    naics_code: str
    agency: str
    incumbent_name: str
    place_of_performance: str
    expiration_date: date
    potential_end_date: date | None = None
    estimated_annual_value: float
    total_obligation: float
    pop_flag: str = ""
    period_years: float | None = None
    remaining_option_years: float | None = None
    in_pursuit_range: bool
    has_option_years: bool
    in_pipeline: bool
    fit_note: str


class MarketBrowseRead(BaseModel):
    window_start: date
    window_end: date
    pursuit_min_annual: float
    pursuit_max_annual: float
    browse_min_annual: float
    browse_max_annual: float
    pages_scanned: int
    candidates_scanned: int
    results: list[BrowseContractRead]


class AppSettingsRead(BaseModel):
    min_award_amount: float
    max_award_amount: float | None = None
    expiration_days: int
    recompete_only: bool = False
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppSettingsUpdate(BaseModel):
    min_award_amount: float = Field(ge=1_000, le=500_000_000)
    max_award_amount: float | None = Field(default=None, ge=1_000, le=500_000_000)
    expiration_days: int = Field(ge=1, le=365)
    recompete_only: bool = False

    @model_validator(mode="after")
    def validate_amount_range(self) -> "AppSettingsUpdate":
        if (
            self.max_award_amount is not None
            and self.max_award_amount < self.min_award_amount
        ):
            raise ValueError("Maximum est. annual value must be greater than or equal to minimum")
        return self


class CleanupLogRead(BaseModel):
    last_run: datetime | None = None
    deleted_watching: int = 0
    deleted_lost: int = 0
    flagged_stale: int = 0
    archived_count: int = 0
    status: str = "unknown"
    message: str | None = None
    details: str | None = None


class WatchlistRead(BaseModel):
    id: int
    contract_name: str
    agency: str
    location_city: str
    location_state: str
    naics_code: str
    incumbent_name: str
    award_amount: float
    expiration_date: date
    expected_repost_start: date
    expected_repost_end: date
    priority: WatchlistPriority
    status: WatchlistStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WatchlistStatusUpdate(BaseModel):
    status: WatchlistStatus


class WatchlistMatchNotification(BaseModel):
    """Contract match payload from GovTracker SAM.gov monitoring."""

    agency: str = Field(min_length=2, max_length=512)
    location_city: str = Field(min_length=1, max_length=256)
    location_state: str = Field(min_length=2, max_length=64)
    naics_code: str = Field(min_length=2, max_length=16)
    sam_notice_id: str | None = Field(default=None, max_length=128)
    contract_title: str | None = Field(default=None, max_length=2000)
    source: str = Field(default="govtracker", max_length=64)

    @field_validator("agency", "location_city", "location_state", "naics_code")
    @classmethod
    def strip_required_fields(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty")
        return cleaned


class WatchlistMatchUpdateResponse(BaseModel):
    updated: bool
    watchlist_ids: list[int]
    status: WatchlistStatus
    matched_count: int
    message: str
