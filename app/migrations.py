import logging

from sqlalchemy import inspect, text

from app.database import engine
from app.models import (
    GS_APP_SETTINGS,
    GS_ARCHIVED_CONTRACTS,
    GS_CLEANUP_LOGS,
    GS_CONTRACTS,
    GS_CONTRACT_STATUS,
    GS_SYNC_LOGS,
    GS_WATCHLIST,
    GS_WATCHLIST_PRIORITY,
    GS_WATCHLIST_STATUS,
)

logger = logging.getLogger(__name__)

TABLE_RENAMES = {
    "contracts": GS_CONTRACTS,
    "archived_contracts": GS_ARCHIVED_CONTRACTS,
    "cleanup_logs": GS_CLEANUP_LOGS,
    "sync_logs": GS_SYNC_LOGS,
    "app_settings": GS_APP_SETTINGS,
    "watchlist": GS_WATCHLIST,
}

ENUM_RENAMES = {
    "contract_status": GS_CONTRACT_STATUS,
    "watchlist_priority": GS_WATCHLIST_PRIORITY,
    "watchlist_status": GS_WATCHLIST_STATUS,
}

NEW_COLUMNS = [
    ("set_aside", "VARCHAR(256) NOT NULL DEFAULT ''"),
    ("extent_competed", "VARCHAR(256) NOT NULL DEFAULT ''"),
    ("solicitation_number", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("pursuit_score", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("notes", "TEXT NOT NULL DEFAULT ''"),
    ("start_date", "DATE"),
    ("total_obligation", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("base_exercised_options_value", "DOUBLE PRECISION"),
    ("base_all_options_value", "DOUBLE PRECISION"),
    ("estimated_annual_value", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("pop_flag", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("potential_end_date", "DATE"),
    ("number_of_offers_received", "INTEGER"),
    ("period_years", "DOUBLE PRECISION"),
    ("remaining_option_years", "DOUBLE PRECISION"),
    ("total_runway_years", "DOUBLE PRECISION"),
    ("recurring_fit", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("recurring_fit_score", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
]

APP_SETTINGS_COLUMNS = [
    ("max_award_amount", "DOUBLE PRECISION"),
]


def _table_exists(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _enum_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT 1 FROM pg_type WHERE typname = :name"),
            {"name": name},
        ).scalar()
    )


def _enum_value_exists(conn, enum_name: str, value: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1 FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = :enum_name AND e.enumlabel = :value
                """
            ),
            {"enum_name": enum_name, "value": value},
        ).scalar()
    )


def _rename_tables(conn, inspector) -> None:
    for old_name, new_name in TABLE_RENAMES.items():
        if _table_exists(inspector, old_name) and not _table_exists(inspector, new_name):
            conn.execute(text(f'ALTER TABLE "{old_name}" RENAME TO "{new_name}"'))
            logger.info("Renamed table %s -> %s", old_name, new_name)


def _rename_enums(conn) -> None:
    for old_name, new_name in ENUM_RENAMES.items():
        if _enum_exists(conn, old_name) and not _enum_exists(conn, new_name):
            conn.execute(text(f'ALTER TYPE "{old_name}" RENAME TO "{new_name}"'))
            logger.info("Renamed enum %s -> %s", old_name, new_name)


def run_migrations() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        _rename_tables(conn, inspector)
        _rename_enums(conn)

    inspector = inspect(engine)
    if not _table_exists(inspector, GS_CONTRACTS):
        return

    existing = {col["name"] for col in inspector.get_columns(GS_CONTRACTS)}
    with engine.begin() as conn:
        for name, col_type in NEW_COLUMNS:
            if name not in existing:
                conn.execute(text(f'ALTER TABLE "{GS_CONTRACTS}" ADD COLUMN {name} {col_type}'))

        if _enum_exists(conn, GS_CONTRACT_STATUS) and not _enum_value_exists(
            conn, GS_CONTRACT_STATUS, "Stale"
        ):
            conn.execute(text(f'ALTER TYPE "{GS_CONTRACT_STATUS}" ADD VALUE \'Stale\''))

    if _table_exists(inspector, GS_APP_SETTINGS):
        settings_columns = {col["name"] for col in inspector.get_columns(GS_APP_SETTINGS)}
        with engine.begin() as conn:
            for name, col_type in APP_SETTINGS_COLUMNS:
                if name not in settings_columns:
                    conn.execute(
                        text(f'ALTER TABLE "{GS_APP_SETTINGS}" ADD COLUMN {name} {col_type}')
                    )
                    logger.info("Added column %s to %s", name, GS_APP_SETTINGS)

            conn.execute(
                text(
                    f'UPDATE "{GS_APP_SETTINGS}" SET max_award_amount = 350000 '
                    "WHERE max_award_amount IS NULL"
                )
            )

    if _table_exists(inspector, GS_CONTRACTS):
        with engine.begin() as conn:
            conn.execute(
                text(
                    f'UPDATE "{GS_CONTRACTS}" SET pursuit_score = 0 '
                    "WHERE estimated_annual_value = 0 AND pursuit_score > 0"
                )
            )
