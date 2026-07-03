import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import ArchivedContract, CleanupLog, Contract, ContractStatus

logger = logging.getLogger(__name__)


def _archive_contract(db: Session, contract: Contract, reason: str) -> None:
    archived = ArchivedContract(
        original_contract_id=contract.id,
        award_id=contract.award_id,
        generated_internal_id=contract.generated_internal_id,
        contract_name=contract.contract_name,
        award_amount=contract.award_amount,
        agency=contract.agency,
        place_of_performance=contract.place_of_performance,
        incumbent_name=contract.incumbent_name,
        expiration_date=contract.expiration_date,
        contracting_office=contract.contracting_office,
        co_name=contract.co_name,
        naics_code=contract.naics_code,
        set_aside=contract.set_aside,
        extent_competed=contract.extent_competed,
        solicitation_number=contract.solicitation_number,
        pursuit_score=contract.pursuit_score,
        notes=contract.notes,
        status_at_archive=contract.status.value,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
        last_synced_at=contract.last_synced_at,
        archive_reason=reason,
        archived_at=datetime.utcnow(),
    )
    db.add(archived)


class CleanupService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run_cleanup(self) -> CleanupLog:
        log = CleanupLog(status="running", started_at=datetime.utcnow())
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)

        today = date.today()
        now = datetime.utcnow()
        expiration_cutoff = today - timedelta(days=90)
        lost_cutoff = now - timedelta(days=90)
        pursuing_cutoff = now - timedelta(days=60)

        deleted_watching = 0
        deleted_lost = 0
        flagged_stale = 0
        archived_count = 0
        stale_award_ids: list[str] = []

        try:
            pursuing_rows = (
                self.db.query(Contract)
                .filter(
                    Contract.status == ContractStatus.PURSUING,
                    Contract.updated_at <= pursuing_cutoff,
                )
                .all()
            )
            for contract in pursuing_rows:
                contract.status = ContractStatus.STALE
                contract.updated_at = now
                flagged_stale += 1
                stale_award_ids.append(contract.award_id)
                logger.warning(
                    "Flagged contract %s (%s) as Stale — Pursuing with no update for 60+ days",
                    contract.award_id,
                    contract.contract_name[:80],
                )

            watching_rows = (
                self.db.query(Contract)
                .filter(
                    Contract.status == ContractStatus.WATCHING,
                    Contract.expiration_date < expiration_cutoff,
                )
                .all()
            )
            for contract in watching_rows:
                _archive_contract(self.db, contract, "watching_expired_90d")
                archived_count += 1
                self.db.delete(contract)
                deleted_watching += 1
                logger.info(
                    "Archived and deleted Watching contract %s (expired %s)",
                    contract.award_id,
                    contract.expiration_date,
                )

            lost_rows = (
                self.db.query(Contract)
                .filter(
                    Contract.status == ContractStatus.LOST,
                    Contract.updated_at <= lost_cutoff,
                )
                .all()
            )
            for contract in lost_rows:
                _archive_contract(self.db, contract, "lost_retention_90d")
                archived_count += 1
                self.db.delete(contract)
                deleted_lost += 1
                logger.info(
                    "Archived and deleted Lost contract %s (last updated %s)",
                    contract.award_id,
                    contract.updated_at,
                )

            log.deleted_watching = deleted_watching
            log.deleted_lost = deleted_lost
            log.flagged_stale = flagged_stale
            log.archived_count = archived_count
            log.status = "success"
            log.message = (
                f"Deleted {deleted_watching} Watching, {deleted_lost} Lost; "
                f"flagged {flagged_stale} Stale; archived {archived_count} records."
            )
            if stale_award_ids:
                log.details = f"Stale award IDs: {', '.join(stale_award_ids[:50])}"
                if len(stale_award_ids) > 50:
                    log.details += f" (+{len(stale_award_ids) - 50} more)"

            logger.info(
                "Cleanup complete — deleted Watching=%s Lost=%s flagged Stale=%s archived=%s",
                deleted_watching,
                deleted_lost,
                flagged_stale,
                archived_count,
            )
        except Exception as exc:
            logger.exception("Cleanup job failed")
            self.db.rollback()
            log.status = "failed"
            log.message = str(exc)
            log.finished_at = datetime.utcnow()
            self.db.add(log)
            self.db.commit()
            raise
        else:
            log.finished_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(log)

        return log

    def get_latest_log(self) -> CleanupLog | None:
        return (
            self.db.query(CleanupLog)
            .order_by(CleanupLog.started_at.desc())
            .first()
        )
