import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Contract, ContractStatus, SyncLog
from app.services.app_settings import get_sync_config
from app.services.scoring import annual_value_in_range
from app.services.sync_lock import acquire_sync_lock, get_running_sync_log, release_sync_lock
from app.services.usaspending import USAspendingClient
from app.services.watchlist import (
    remove_out_of_window_watchlist,
    remove_stale_watchlist,
    upsert_watchlist,
)

logger = logging.getLogger(__name__)

WATCHLIST_ONLY_FIELDS = frozenset({"location_city", "location_state"})


class ContractSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sync_config = get_sync_config(db)
        self.client = self._build_client()

    def _build_client(self) -> USAspendingClient:
        return USAspendingClient(
            min_award_amount=self.sync_config.min_award_amount,
            max_award_amount=self.sync_config.max_award_amount,
            max_annual_value=self.sync_config.max_award_amount or 350_000,
        )

    async def run_sync(self, *, lock_held: bool = False, log_id: int | None = None) -> SyncLog:
        if not lock_held:
            await acquire_sync_lock(self.db)

        try:
            return await self._run_sync_locked(log_id=log_id)
        finally:
            release_sync_lock()

    async def _run_sync_locked(self, log_id: int | None = None) -> SyncLog:
        self.sync_config = get_sync_config(self.db)
        self.client = self._build_client()

        if log_id is not None:
            log = self.db.query(SyncLog).filter(SyncLog.id == log_id).one()
        else:
            log = SyncLog(
                status="running",
                started_at=datetime.utcnow(),
                message="Searching USAspending…",
            )
            self.db.add(log)
            self.db.commit()
            self.db.refresh(log)

        window_start = date.today()
        window_end = window_start + timedelta(days=self.sync_config.expiration_days)
        min_annual = self.sync_config.min_award_amount
        max_annual = self.sync_config.max_award_amount

        try:
            annual_range = (
                f"${min_annual:,.0f}–${max_annual:,.0f}/yr est."
                if max_annual is not None
                else f"${min_annual:,.0f}+/yr est."
            )
            logger.info(
                "Starting sync — window %s to %s, annual range %s (log id %s)",
                window_start,
                window_end,
                annual_range,
                log.id,
            )
            log.message = "Contacting USAspending API…"
            self.db.commit()

            contracts_found = 0
            upserted = 0
            watchlist_upserted = 0
            pages_scanned = 0
            skipped_annual = 0

            async for page_batch, pages, naics_code in self.client.stream_expiring_contracts(
                window_start, window_end
            ):
                pages_scanned = pages
                enriched_fields = await self.client.enrich_awards_batch(page_batch)

                for fields in enriched_fields:
                    if not annual_value_in_range(
                        fields.get("estimated_annual_value"),
                        min_annual,
                        max_annual,
                    ):
                        skipped_annual += 1
                        continue
                    upserted += self._upsert_contract(fields, commit=False)
                    watchlist_upserted += upsert_watchlist(self.db, fields, commit=False)

                contracts_found += len(page_batch)
                log.contracts_found = contracts_found
                log.contracts_upserted = upserted
                log.pages_scanned = pages_scanned
                if upserted == 0:
                    log.message = (
                        f"Scanning page {pages_scanned} (NAICS {naics_code}) — "
                        f"skipping expired contracts until {window_start}–{window_end} window…"
                    )
                else:
                    log.message = (
                        f"Loaded {upserted} contracts so far "
                        f"({pages_scanned} API pages scanned, NAICS {naics_code})…"
                    )
                self.db.commit()

                logger.info(
                    "Scanned page %s (NAICS %s) — %s in batch, %s saved, %s outside annual range",
                    pages_scanned,
                    naics_code,
                    len(page_batch),
                    upserted,
                    skipped_annual,
                )

            expired_removed = self._remove_stale_contracts(window_start)
            out_of_window_removed = self._remove_out_of_window_contracts(window_end)
            outside_annual_removed = self._remove_outside_annual_range(min_annual, max_annual)
            watchlist_stale = remove_stale_watchlist(self.db, window_start)
            watchlist_outside = remove_out_of_window_watchlist(self.db, window_end)

            log.contracts_upserted = upserted
            log.pages_scanned = pages_scanned
            log.status = "success"
            log.message = (
                f"Upserted {upserted} contracts and {watchlist_upserted} watchlist entries. "
                f"Skipped {skipped_annual} outside ${min_annual:,.0f}"
                f"{f'–${max_annual:,.0f}' if max_annual is not None else '+'}/yr est. annual range. "
                f"Removed {expired_removed} expired, {out_of_window_removed} outside window"
                f"{f', {outside_annual_removed} outside annual range' if outside_annual_removed else ''}. "
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
            existing.start_date = fields.get("start_date")
            existing.total_obligation = fields.get("total_obligation", fields["award_amount"])
            existing.base_exercised_options_value = fields.get("base_exercised_options_value")
            existing.base_all_options_value = fields.get("base_all_options_value")
            existing.estimated_annual_value = fields.get("estimated_annual_value", 0.0)
            existing.pop_flag = fields.get("pop_flag", "")
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
            **{key: value for key, value in fields.items() if key not in WATCHLIST_ONLY_FIELDS},
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

    def _remove_outside_annual_range(
        self,
        min_annual_value: float,
        max_annual_value: float | None,
    ) -> int:
        query = self.db.query(Contract).filter(Contract.estimated_annual_value < min_annual_value)
        if max_annual_value is not None:
            query = query.filter(Contract.estimated_annual_value > max_annual_value)
        outside = query.all()
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
