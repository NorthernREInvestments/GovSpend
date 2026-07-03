import logging
import os
from urllib.parse import urlparse

from sqlalchemy import text

from app.database import Base, engine
from app.migrations import run_migrations

logger = logging.getLogger(__name__)

_db_ready = False


def is_db_ready() -> bool:
    return _db_ready


def log_database_target() -> None:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        logger.warning("DATABASE_URL is not set — using default localhost (Railway: add PostgreSQL plugin)")
        return
    parsed = urlparse(raw.replace("postgres://", "postgresql://", 1))
    host = parsed.hostname or "unknown"
    port = parsed.port or "default"
    db = (parsed.path or "").lstrip("/") or "unknown"
    logger.info("Database target: %s:%s/%s", host, port, db)


def init_database(max_attempts: int = 30, delay_seconds: float = 2.0) -> bool:
    global _db_ready
    log_database_target()

    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            run_migrations()
            _db_ready = True
            logger.info("Database ready")
            return True
        except Exception as exc:
            logger.warning("Database init attempt %s/%s failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                import time

                time.sleep(delay_seconds)

    logger.error(
        "Database never became available. Add a PostgreSQL service on Railway and link DATABASE_URL."
    )
    _db_ready = False
    return False
