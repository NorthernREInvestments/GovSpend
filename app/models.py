import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# PostgreSQL table names (gs_ prefix for shared-database isolation)
GS_CONTRACTS = "gs_contracts"
GS_ARCHIVED_CONTRACTS = "gs_archived_contracts"
GS_CLEANUP_LOGS = "gs_cleanup_logs"
GS_SYNC_LOGS = "gs_sync_logs"
GS_APP_SETTINGS = "gs_app_settings"
GS_WATCHLIST = "gs_watchlist"

# PostgreSQL enum type names
GS_CONTRACT_STATUS = "gs_contract_status"
GS_WATCHLIST_PRIORITY = "gs_watchlist_priority"
GS_WATCHLIST_STATUS = "gs_watchlist_status"


class ContractStatus(str, enum.Enum):
    WATCHING = "Watching"
    ACTIVE = "Active"
    PURSUING = "Pursuing"
    STALE = "Stale"
    WON = "Won"
    LOST = "Lost"


class WatchlistPriority(str, enum.Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class WatchlistStatus(str, enum.Enum):
    WATCHING = "Watching"
    FOUND_ON_SAM = "Found on SAM"
    PURSUING = "Pursuing"
    WON = "Won"
    LOST = "Lost"


class Contract(Base):
    __tablename__ = GS_CONTRACTS
    __table_args__ = (
        UniqueConstraint("award_id", name="uq_gs_contracts_award_id"),
        Index("ix_gs_contracts_expiration_date", "expiration_date"),
        Index("ix_gs_contracts_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    award_id: Mapped[str] = mapped_column(String(128), nullable=False)
    generated_internal_id: Mapped[str | None] = mapped_column(String(256))
    contract_name: Mapped[str] = mapped_column(Text, nullable=False)
    award_amount: Mapped[float] = mapped_column(Float, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_obligation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    base_exercised_options_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_all_options_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_annual_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pop_flag: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    period_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_option_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_runway_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    recurring_fit: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    recurring_fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recurrence_pattern: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    option_extensions_count: Mapped[int] = mapped_column(nullable=False, default=0)
    prior_similar_awards_count: Mapped[int] = mapped_column(nullable=False, default=0)
    agency: Mapped[str] = mapped_column(String(512), nullable=False)
    place_of_performance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    incumbent_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    expiration_date: Mapped[date] = mapped_column(Date, nullable=False)
    potential_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    number_of_offers_received: Mapped[int | None] = mapped_column(nullable=True)
    contracting_office: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    co_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    naics_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    set_aside: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    extent_competed: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    solicitation_number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pursuit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name=GS_CONTRACT_STATUS, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ContractStatus.WATCHING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ArchivedContract(Base):
    __tablename__ = GS_ARCHIVED_CONTRACTS

    id: Mapped[int] = mapped_column(primary_key=True)
    original_contract_id: Mapped[int] = mapped_column(nullable=False)
    award_id: Mapped[str] = mapped_column(String(128), nullable=False)
    generated_internal_id: Mapped[str | None] = mapped_column(String(256))
    contract_name: Mapped[str] = mapped_column(Text, nullable=False)
    award_amount: Mapped[float] = mapped_column(Float, nullable=False)
    agency: Mapped[str] = mapped_column(String(512), nullable=False)
    place_of_performance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    incumbent_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    expiration_date: Mapped[date] = mapped_column(Date, nullable=False)
    contracting_office: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    co_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    naics_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    set_aside: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    extent_competed: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    solicitation_number: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pursuit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status_at_archive: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    archive_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CleanupLog(Base):
    __tablename__ = GS_CLEANUP_LOGS

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_watching: Mapped[int] = mapped_column(default=0)
    deleted_lost: Mapped[int] = mapped_column(default=0)
    flagged_stale: Mapped[int] = mapped_column(default=0)
    archived_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text)


class SyncLog(Base):
    __tablename__ = GS_SYNC_LOGS

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    contracts_found: Mapped[int] = mapped_column(default=0)
    contracts_upserted: Mapped[int] = mapped_column(default=0)
    pages_scanned: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")
    message: Mapped[str | None] = mapped_column(Text)


class AppSettings(Base):
    __tablename__ = GS_APP_SETTINGS

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    min_award_amount: Mapped[float] = mapped_column(Float, nullable=False, default=50_000)
    max_award_amount: Mapped[float | None] = mapped_column(Float, nullable=True, default=350_000)
    expiration_days: Mapped[int] = mapped_column(nullable=False, default=60)
    recompete_only: Mapped[bool] = mapped_column(nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Watchlist(Base):
    __tablename__ = GS_WATCHLIST
    __table_args__ = (
        UniqueConstraint("award_id", name="uq_gs_watchlist_award_id"),
        Index("ix_gs_watchlist_expiration_date", "expiration_date"),
        Index("ix_gs_watchlist_priority", "priority"),
        Index("ix_gs_watchlist_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    award_id: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_name: Mapped[str] = mapped_column(Text, nullable=False)
    agency: Mapped[str] = mapped_column(String(512), nullable=False)
    location_city: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    location_state: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    naics_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    incumbent_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    award_amount: Mapped[float] = mapped_column(Float, nullable=False)
    expiration_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_repost_start: Mapped[date] = mapped_column(Date, nullable=False)
    expected_repost_end: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[WatchlistPriority] = mapped_column(
        Enum(WatchlistPriority, name=GS_WATCHLIST_PRIORITY, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status: Mapped[WatchlistStatus] = mapped_column(
        Enum(WatchlistStatus, name=GS_WATCHLIST_STATUS, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=WatchlistStatus.WATCHING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
