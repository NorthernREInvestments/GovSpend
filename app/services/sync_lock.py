import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import SyncLog

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()
STALE_SYNC_HOURS = 6


class SyncInProgressError(Exception):
    def __init__(self, log: SyncLog | None = None):
        self.log = log
        super().__init__("A contract sync is already running")


def _mark_stale_running_logs(db: Session) -> None:
    cutoff = datetime.utcnow() - timedelta(hours=STALE_SYNC_HOURS)
    stale = (
        db.query(SyncLog)
        .filter(SyncLog.status == "running", SyncLog.started_at < cutoff)
        .all()
    )
    for log in stale:
        log.status = "failed"
        log.message = "Marked failed — sync exceeded time limit (likely interrupted by deploy)"
        log.finished_at = datetime.utcnow()
    if stale:
        db.commit()
        logger.warning("Marked %s stale sync log(s) as failed", len(stale))


def get_running_sync_log(db: Session) -> SyncLog | None:
    _mark_stale_running_logs(db)
    return (
        db.query(SyncLog)
        .filter(SyncLog.status == "running")
        .order_by(SyncLog.started_at.desc())
        .first()
    )


def is_sync_running(db: Session) -> bool:
    return get_running_sync_log(db) is not None


async def acquire_sync_lock(db: Session) -> None:
    """Raise SyncInProgressError if a sync is already active."""
    running = get_running_sync_log(db)
    if running:
        raise SyncInProgressError(running)

    if _sync_lock.locked():
        running = get_running_sync_log(db)
        raise SyncInProgressError(running)

    await _sync_lock.acquire()
    try:
        running = get_running_sync_log(db)
        if running:
            raise SyncInProgressError(running)
    except Exception:
        release_sync_lock()
        raise


def release_sync_lock() -> None:
    if _sync_lock.locked():
        _sync_lock.release()
