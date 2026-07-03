import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.services.app_settings import get_sync_config
from app.models import Contract, ContractStatus, SyncLog
from app.services.scoring import compute_pursuit_score
from app.services.usaspending import USAspendingClient, map_award_to_contract_fields
from app.services.watchlist import (
    remove_out_of_window_watchlist,
    remove_stale_watchlist,
    upsert_watchlist,
)

logger = logging.getLogger(__name__)


class ContractSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sync_config = get_sync_config(db)
        self.client = USAspendingClient(min_award_amount=self.sync_config.min_award_amount)

    async def run_sync(self) -> SyncLog:
        self.sync_config = get_sync_config(self.db)
        self.client = USAspendingClient(min_award_amount=self.sync_config.min_award_amount)

        log = SyncLog(status="running", started_at=datetime.utcnow())
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)

        window_start = date.today()
        window_end = window_start + timedelta(days=self.sync_config.expiration_days)

        try:
            awards, pages_scanned = await self.client.search_expiring_contracts(window_start, window_end)
            log.contracts_found = len(awards)
            upserted = 0
            watchlist_upserted = 0

            for award in awards:
                generated_id = award.get("generated_internal_id")
                enrichment = await self.client.enrich_award(generated_id or "")
                fields = map_award_to_contract_fields(award, enrichment)
                upserted += self._upsert_contract(fields)
                watchlist_upserted += upsert_watchlist(self.db, fields)

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
        except Exception as exc:
            logger.exception("Contract sync failed")
            log.status = "failed"
            log.message = str(exc)
            log.finished_at = datetime.utcnow()
            self.db.add(log)
            self.db.commit()
            raise
        else:
            log.finished_at = datetime.utcnow()
            self.db.add(log)
            self.db.commit()
            self.db.refresh(log)

        return log

    def _upsert_contract(self, fields: dict) -> int:
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
            existing.contracting_office = fields["contracting_office"]
            if fields["co_name"]:
                existing.co_name = fields["co_name"]
            existing.naics_code = fields["naics_code"]
            existing.set_aside = fields["set_aside"]
            existing.extent_competed = fields["extent_competed"]
            existing.solicitation_number = fields["solicitation_number"]
            existing.pursuit_score = fields["pursuit_score"]
            existing.last_synced_at = now
            existing.updated_at = now
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
