import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Contract, ContractStatus, SyncLog
from app.services.app_settings import get_sync_config
from app.services.sync_lock import acquire_sync_lock, get_running_sync_log, release_sync_lock
from app.services.usaspending import USAspendingClient, map_award_to_contract_fields
from app.services.watchlist import (
    remove_out_of_window_watchlist,
    remove_stale_watchlist,
    upsert_watchlist,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
EMPTY_ENRICHMENT: dict[str, str] = {
    "contracting_office": "",
    "co_name": "",
    "set_aside": "",
    "extent_competed": "",
    "solicitation_number": "",
}


class ContractSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sync_config = get_sync_config(db)
        self.client = USAspendingClient(min_award_amount=self.sync_config.min_award_amount)

    async def run_sync(self) -> SyncLog:
        await acquire_sync_lock(self.db)

        try:
            return await self._run_sync_locked()
        finally:
            release_sync_lock()

    async def _run_sync_locked(self) -> SyncLog:
        self.sync_config = get_sync_config(self.db)
        self.client = USAspendingClient(min_award_amount=self.sync_config.min_award_amount)

        log = SyncLog(status="running", started_at=datetime.utcnow())
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)

        window_start = date.today()
        window_end = window_start + timedelta(days=self.sync_config.expiration_days)

        try:
            logger.info(
                "Starting sync — window %s to %s, min award $%s",
                window_start,
                window_end,
                f"{self.sync_config.min_award_amount:,.0f}",
            )
            awards, pages_scanned = await self.client.search_expiring_contracts(window_start, window_end)
            log.contracts_found = len(awards)
            self.db.commit()
            logger.info("Found %s contracts across %s NAICS page scans", len(awards), pages_scanned)

            upserted = 0
            watchlist_upserted = 0
            total = len(awards)

            for index, award in enumerate(awards, start=1):
                fields = map_award_to_contract_fields(award, EMPTY_ENRICHMENT)
                upserted += self._upsert_contract(fields, commit=False)
                watchlist_upserted += upsert_watchlist(self.db, fields, commit=False)

                if index % BATCH_SIZE == 0 or index == total:
                    self.db.commit()
                    logger.info("Saved %s/%s contracts to database", index, total)

            expired_removed = self._remove_stale_contracts(window_start)
            out_of_window_removed = self._remove_out_of_window_contracts(window_end)
            watchlist_stale = remove_stale_watchlist(self.db, window_start)
            watchlist_outside = remove_out_of_window_watchlist(self.db, window_end)

            log.contracts_upserted = upserted
            log.pages_scanned = pages_scanned
            log.status = "success"
            log.message = (
                f"Upserted {upserted} contracts and {watchlist_upserted} watchlist entries. "
                f"Removed {expired_removed} expired contracts, {out_of_window_removed} outside window. "
                f"Watchlist cleanup: {watchlist_stale} expired, {watchlist_outside} outside window."
            )
            log.finished_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(log)
            logger.info("Sync complete — %s", log.message)
        except Exception as exc:
            logger.exception("Contract sync failed")
            self.db.rollback()
            log.status = "failed"
            log.message = str(exc)
            log.finished_at = datetime.utcnow()
            self.db.add(log)
            self.db.commit()
            raise

        return log

    def _upsert_contract(self, fields: dict, *, commit: bool = True) -> int:
        existing = (
            self.db.query(Contract)
            .filter(Contract.award_id == fields["award_id"])
            .one_or_none()
        )
        now = datetime.utcnow()

        if existing:
            existing.generated_internal_id = fields.get("generated_internal_id")
            existing.contract_name = fields["contract_name"]
            existing.award_amount = fields["award_amount"]
            existing.agency = fields["agency"]
            existing.place_of_performance = fields["place_of_performance"]
            existing.incumbent_name = fields["incumbent_name"]
            existing.expiration_date = fields["expiration_date"]
            if fields["contracting_office"]:
                existing.contracting_office = fields["contracting_office"]
            if fields["co_name"]:
                existing.co_name = fields["co_name"]
            existing.naics_code = fields["naics_code"]
            if fields.get("set_aside"):
                existing.set_aside = fields["set_aside"]
            if fields.get("extent_competed"):
                existing.extent_competed = fields["extent_competed"]
            if fields.get("solicitation_number"):
                existing.solicitation_number = fields["solicitation_number"]
            existing.pursuit_score = fields["pursuit_score"]
            existing.last_synced_at = now
            existing.updated_at = now
            if commit:
                self.db.commit()
            return 1

        contract = Contract(
            **fields,
            status=ContractStatus.WATCHING,
            last_synced_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(contract)
        if commit:
            self.db.commit()
        return 1

    def _remove_stale_contracts(self, window_start: date) -> int:
        stale = (
            self.db.query(Contract)
            .filter(Contract.expiration_date < window_start)
            .all()
        )
        for contract in stale:
            self.db.delete(contract)
        self.db.commit()
        return len(stale)

    def _remove_out_of_window_contracts(self, window_end: date) -> int:
        outside = (
            self.db.query(Contract)
            .filter(Contract.expiration_date > window_end)
            .all()
        )
        for contract in outside:
            self.db.delete(contract)
        self.db.commit()
        return len(outside)

    def get_latest_sync_log(self) -> SyncLog | None:
        return (
            self.db.query(SyncLog)
            .order_by(SyncLog.started_at.desc())
            .first()
        )

    def get_running_sync_log(self) -> SyncLog | None:
        return get_running_sync_log(self.db)
