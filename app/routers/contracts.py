import asyncio
import csv
import io
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Contract, ContractStatus, SyncLog, Watchlist, WatchlistPriority, WatchlistStatus
from app.schemas import (
    AppSettingsRead,
    AppSettingsUpdate,
    ContractNotesUpdate,
    ContractRead,
    ContractStatusUpdate,
    DashboardLiveRead,
    DashboardStats,
    SyncStatusRead,
    CleanupLogRead,
    WatchlistRead,
    WatchlistMatchNotification,
    WatchlistMatchUpdateResponse,
    WatchlistStatusUpdate,
)
from app.services.dashboard_data import build_dashboard_data, pursuit_contracts_query
from app.services.govtracker import apply_govtracker_match, find_watchlist_matches
from app.services.app_settings import get_or_create_app_settings, update_app_settings
from app.services.cleanup import CleanupService
from app.services.scoring import priority_tier, usaspending_award_url
from app.services.sync import ContractSyncService
from app.services.sync_lock import SyncInProgressError, acquire_sync_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["contracts"])


def _pursuit_query(db: Session):
    return pursuit_contracts_query(db)


@router.get("/dashboard/live", response_model=DashboardLiveRead)
def dashboard_live(db: Session = Depends(get_db)):
    data = build_dashboard_data(db)
    return DashboardLiveRead(
        contracts=[ContractRead.model_validate(contract) for contract in data["contracts"]],
        hot_leads=[ContractRead.model_validate(lead) for lead in data["hot_leads"]],
        stats=data["stats"],
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


@router.get("/cleanup/status", response_model=CleanupLogRead)
def cleanup_status(db: Session = Depends(get_db)):
    log = CleanupService(db).get_latest_log()
    if not log:
        return CleanupLogRead(status="never_run")
    return CleanupLogRead(
        last_run=log.finished_at or log.started_at,
        deleted_watching=log.deleted_watching,
        deleted_lost=log.deleted_lost,
        flagged_stale=log.flagged_stale,
        archived_count=log.archived_count,
        status=log.status,
        message=log.message,
        details=log.details,
    )


@router.post("/cleanup/run", response_model=CleanupLogRead)
def run_cleanup(db: Session = Depends(get_db)):
    log = CleanupService(db).run_cleanup()
    return CleanupLogRead(
        last_run=log.finished_at or log.started_at,
        deleted_watching=log.deleted_watching,
        deleted_lost=log.deleted_lost,
        flagged_stale=log.flagged_stale,
        archived_count=log.archived_count,
        status=log.status,
        message=log.message,
        details=log.details,
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


@router.post("/watchlist/update-status", response_model=WatchlistMatchUpdateResponse)
def govtracker_update_watchlist_status(
    payload: WatchlistMatchNotification,
    db: Session = Depends(get_db),
):
    matches = find_watchlist_matches(db, payload)
    if not matches:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "No watchlist record matches agency, location, and NAICS",
                "agency": payload.agency,
                "location_city": payload.location_city,
                "location_state": payload.location_state,
                "naics_code": payload.naics_code,
            },
        )

    active_matches = [m for m in matches if m.status not in (WatchlistStatus.WON, WatchlistStatus.LOST)]
    if not active_matches:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Matching watchlist records are closed (Won/Lost)",
                "watchlist_ids": [m.id for m in matches],
            },
        )

    updated = apply_govtracker_match(db, payload)
    return WatchlistMatchUpdateResponse(
        updated=True,
        watchlist_ids=[entry.id for entry in updated],
        status=WatchlistStatus.FOUND_ON_SAM,
        matched_count=len(updated),
        message=f"Updated {len(updated)} watchlist record(s) to Found on SAM",
    )


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
        max_award_amount=payload.max_award_amount,
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
    service = ContractSyncService(db)
    running = service.get_running_sync_log()
    if running:
        return SyncStatusRead(
            last_sync=running.started_at,
            contracts_found=running.contracts_found,
            contracts_upserted=running.contracts_upserted,
            pages_scanned=running.pages_scanned,
            status="running",
            message=running.message or "Sync in progress…",
        )
    log = service.get_latest_sync_log()
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


@router.post("/sync/run", response_model=SyncStatusRead, status_code=status.HTTP_202_ACCEPTED)
async def run_sync(db: Session = Depends(get_db)):
    try:
        await acquire_sync_lock(db)
    except SyncInProgressError as exc:
        running = exc.log
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Sync already in progress",
                "started_at": running.started_at.isoformat() if running and running.started_at else None,
                "contracts_found": running.contracts_found if running else 0,
                "contracts_upserted": running.contracts_upserted if running else 0,
            },
        ) from exc

    log = SyncLog(
        status="running",
        started_at=datetime.utcnow(),
        message="Searching USAspending…",
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    async def _background_sync(sync_log_id: int) -> None:
        bg_db = SessionLocal()
        try:
            logger.info("Background sync task started for log id %s", sync_log_id)
            await ContractSyncService(bg_db).run_sync(lock_held=True, log_id=sync_log_id)
        except Exception:
            logger.exception("Background contract sync failed for log id %s", sync_log_id)
        finally:
            bg_db.close()

    asyncio.create_task(_background_sync(log.id))

    return SyncStatusRead(
        last_sync=log.started_at,
        contracts_found=log.contracts_found,
        contracts_upserted=log.contracts_upserted,
        pages_scanned=log.pages_scanned,
        status="running",
        message=log.message,
    )
