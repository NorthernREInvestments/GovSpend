import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ContractStatus(str, enum.Enum):
    WATCHING = "Watching"
    ACTIVE = "Active"
    PURSUING = "Pursuing"
    WON = "Won"
    LOST = "Lost"


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("award_id", name="uq_contracts_award_id"),
        Index("ix_contracts_expiration_date", "expiration_date"),
        Index("ix_contracts_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
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
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name="contract_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ContractStatus.WATCHING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    contracts_found: Mapped[int] = mapped_column(default=0)
    contracts_upserted: Mapped[int] = mapped_column(default=0)
    pages_scanned: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")
    message: Mapped[str | None] = mapped_column(Text)
