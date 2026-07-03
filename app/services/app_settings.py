from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSettings


@dataclass
class SyncConfig:
    min_award_amount: float
    max_award_amount: float | None
    expiration_days: int


def get_sync_config(db: Session) -> SyncConfig:
    row = get_or_create_app_settings(db)
    return SyncConfig(
        min_award_amount=row.min_award_amount,
        max_award_amount=row.max_award_amount,
        expiration_days=row.expiration_days,
    )


def get_or_create_app_settings(db: Session) -> AppSettings:
    row = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
    if row:
        return row
    row = AppSettings(
        id=1,
        min_award_amount=settings.min_award_amount,
        max_award_amount=settings.max_award_amount,
        expiration_days=settings.expiration_days,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_app_settings(
    db: Session,
    min_award_amount: float,
    expiration_days: int,
    max_award_amount: float | None = None,
) -> AppSettings:
    row = get_or_create_app_settings(db)
    row.min_award_amount = min_award_amount
    row.max_award_amount = max_award_amount
    row.expiration_days = expiration_days
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row
