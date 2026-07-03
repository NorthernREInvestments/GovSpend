from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models import ContractStatus, WatchlistPriority, WatchlistStatus


class ContractBase(BaseModel):
    contract_name: str
    award_amount: float
    agency: str
    place_of_performance: str
    incumbent_name: str
    expiration_date: date
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


class AppSettingsRead(BaseModel):
    min_award_amount: float
    expiration_days: int
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AppSettingsUpdate(BaseModel):
    min_award_amount: float = Field(ge=1_000, le=500_000_000)
    expiration_days: int = Field(ge=1, le=365)


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
