from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models import ContractStatus


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
