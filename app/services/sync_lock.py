import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import SyncLog

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()
STALE_SYNC_HOURS = 3
STALE_NO_PROGRESS_MINUTES = 25


def touch_sync_progress(log: SyncLog) -> None:
    log.progress_at = datetime.utcnow()


def _progress_stale(log: SyncLog, *, now: datetime, no_progress_cutoff: datetime) -> bool:
    progress_at = log.progress_at or log.started_at
    return progress_at < no_progress_cutoff


class SyncInProgressError(Exception):
    def __init__(self, log: SyncLog | None = None):
        self.log = log
        super().__init__("A contract sync is already running")


def is_sync_lock_held() -> bool:
    return _sync_lock.locked()


def _fail_running_log(log: SyncLog, message: str) -> None:
    log.status = "failed"
    log.message = message
    log.finished_at = datetime.utcnow()


def _mark_stale_running_logs(db: Session) -> None:
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=STALE_SYNC_HOURS)
    no_progress_cutoff = now - timedelta(minutes=STALE_NO_PROGRESS_MINUTES)
    running_logs = db.query(SyncLog).filter(SyncLog.status == "running").all()
    stale: list[SyncLog] = []

    for log in running_logs:
        if not is_sync_lock_held():
            stale.append(log)
            _fail_running_log(
                log,
                "Interrupted — no active sync in this server process (likely redeploy)",
            )
            continue
        if log.started_at < cutoff:
            stale.append(log)
            _fail_running_log(
                log,
                "Marked failed — sync exceeded time limit (likely interrupted by deploy)",
            )
        elif _progress_stale(log, now=now, no_progress_cutoff=no_progress_cutoff):
            stale.append(log)
            _fail_running_log(
                log,
                "Marked failed — sync stopped making progress. Click Refresh Now to run again.",
            )

    if stale:
        db.commit()
        logger.warning("Marked %s stale sync log(s) as failed", len(stale))


def clear_orphaned_running_syncs(db: Session) -> int:
    """Mark running sync logs failed when this process is not actively syncing."""
    running_logs = db.query(SyncLog).filter(SyncLog.status == "running").all()
    if is_sync_lock_held() or not running_logs:
        return 0

    for log in running_logs:
        _fail_running_log(
            log,
            "Interrupted — server restarted during sync. Click Refresh Now to run again.",
        )
    db.commit()
    logger.warning("Cleared %s orphaned running sync log(s) on startup", len(running_logs))
    return len(running_logs)


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
