import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Contract, ContractStatus, Watchlist, WatchlistPriority
from app.schemas import (
    AppSettingsRead,
    AppSettingsUpdate,
    ContractNotesUpdate,
    ContractRead,
    ContractStatusUpdate,
    DashboardStats,
    SyncStatusRead,
    WatchlistRead,
    WatchlistStatusUpdate,
)
from app.services.app_settings import get_or_create_app_settings, update_app_settings
from app.services.scoring import priority_tier, usaspending_award_url
from app.services.sync import ContractSyncService

router = APIRouter(prefix="/api", tags=["contracts"])


def _pursuit_query(db: Session):
    return db.query(Contract).order_by(
        Contract.pursuit_score.desc(),
        Contract.expiration_date.asc(),
        Contract.award_amount.desc(),
    )


def _watchlist_query(db: Session):
    priority_order = {
        WatchlistPriority.HIGH: 0,
        WatchlistPriority.MEDIUM: 1,
        WatchlistPriority.LOW: 2,
    }
    rows = db.query(Watchlist).all()
    return sorted(
        rows,
        key=lambda w: (
            priority_order.get(w.priority, 3),
            w.expiration_date,
            -w.award_amount,
        ),
    )


@router.get("/watchlist", response_model=list[WatchlistRead])
def list_watchlist(
    priority: WatchlistPriority | None = None,
    db: Session = Depends(get_db),
):
    rows = _watchlist_query(db)
    if priority:
        rows = [r for r in rows if r.priority == priority]
    return rows


@router.patch("/watchlist/{watchlist_id}/status", response_model=WatchlistRead)
def update_watchlist_status(
    watchlist_id: int,
    payload: WatchlistStatusUpdate,
    db: Session = Depends(get_db),
):
    entry = db.query(Watchlist).filter(Watchlist.id == watchlist_id).one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    entry.status = payload.status
    entry.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/settings", response_model=AppSettingsRead)
def get_settings(db: Session = Depends(get_db)):
    return get_or_create_app_settings(db)


@router.patch("/settings", response_model=AppSettingsRead)
def patch_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db)):
    return update_app_settings(
        db,
        min_award_amount=payload.min_award_amount,
        expiration_days=payload.expiration_days,
    )


@router.get("/contracts", response_model=list[ContractRead])
def list_contracts(
    status: ContractStatus | None = None,
    db: Session = Depends(get_db),
):
    query = _pursuit_query(db)
    if status:
        query = query.filter(Contract.status == status)
    return query.all()


@router.patch("/contracts/{contract_id}/status", response_model=ContractRead)
def update_contract_status(
    contract_id: int,
    payload: ContractStatusUpdate,
    db: Session = Depends(get_db),
):
    contract = db.query(Contract).filter(Contract.id == contract_id).one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    contract.status = payload.status
    contract.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(contract)
    return contract


@router.patch("/contracts/{contract_id}/notes", response_model=ContractRead)
def update_contract_notes(
    contract_id: int,
    payload: ContractNotesUpdate,
    db: Session = Depends(get_db),
):
    contract = db.query(Contract).filter(Contract.id == contract_id).one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    contract.notes = payload.notes.strip()
    contract.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(contract)
    return contract


@router.get("/contracts/export")
def export_contracts(db: Session = Depends(get_db)):
    contracts = _pursuit_query(db).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Priority",
        "Pursuit Score",
        "Expiration",
        "Days Left",
        "Award Amount",
        "Contract",
        "Award ID",
        "Agency",
        "Incumbent",
        "Place of Performance",
        "Contracting Office",
        "CO Name",
        "Set-Aside",
        "Competition",
        "Solicitation",
        "NAICS",
        "Status",
        "Notes",
        "USAspending URL",
    ])
    today = date.today()
    for c in contracts:
        days_left = (c.expiration_date - today).days
        writer.writerow([
            priority_tier(c.award_amount, c.expiration_date, today),
            c.pursuit_score,
            c.expiration_date.isoformat(),
            days_left,
            c.award_amount,
            c.contract_name,
            c.award_id,
            c.agency,
            c.incumbent_name,
            c.place_of_performance,
            c.contracting_office,
            c.co_name,
            c.set_aside,
            c.extent_competed,
            c.solicitation_number,
            c.naics_code,
            c.status.value,
            c.notes,
            usaspending_award_url(c.generated_internal_id),
        ])
    output.seek(0)
    filename = f"govspend-opportunities-{today.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    contracts = db.query(Contract).all()
    today = date.today()
    if today.month == 12:
        month_end = date(today.year, 12, 31)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    by_status: dict[str, int] = {status.value: 0 for status in ContractStatus}
    total_value = 0.0
    expiring_this_month = 0

    for contract in contracts:
        total_value += contract.award_amount
        by_status[contract.status.value] = by_status.get(contract.status.value, 0) + 1
        if today <= contract.expiration_date <= month_end:
            expiring_this_month += 1

    return DashboardStats(
        total_contracts=len(contracts),
        total_value=total_value,
        expiring_this_month=expiring_this_month,
        by_status=by_status,
    )


@router.get("/sync/status", response_model=SyncStatusRead)
def sync_status(db: Session = Depends(get_db)):
    log = ContractSyncService(db).get_latest_sync_log()
    if not log:
        return SyncStatusRead(status="never_run")
    return SyncStatusRead(
        last_sync=log.finished_at or log.started_at,
        contracts_found=log.contracts_found,
        contracts_upserted=log.contracts_upserted,
        pages_scanned=log.pages_scanned,
        status=log.status,
        message=log.message,
    )


@router.post("/sync/run", response_model=SyncStatusRead)
async def run_sync(db: Session = Depends(get_db)):
    log = await ContractSyncService(db).run_sync()
    return SyncStatusRead(
        last_sync=log.finished_at or log.started_at,
        contracts_found=log.contracts_found,
        contracts_upserted=log.contracts_upserted,
        pages_scanned=log.pages_scanned,
        status=log.status,
        message=log.message,
    )
