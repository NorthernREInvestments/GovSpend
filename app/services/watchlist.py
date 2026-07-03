from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import Watchlist, WatchlistPriority, WatchlistStatus
from app.services.scoring import expected_repost_dates, watchlist_priority


def build_watchlist_fields(contract_fields: dict, today: date | None = None) -> dict:
    expiration = contract_fields["expiration_date"]
    repost_start, repost_end = expected_repost_dates(expiration)
    priority_label = watchlist_priority(
        contract_fields["award_amount"],
        expiration,
        today=today,
    )

    return {
        "award_id": contract_fields["award_id"],
        "contract_name": contract_fields["contract_name"],
        "agency": contract_fields["agency"],
        "location_city": contract_fields.get("location_city", ""),
        "location_state": contract_fields.get("location_state", ""),
        "naics_code": contract_fields["naics_code"],
        "incumbent_name": contract_fields["incumbent_name"],
        "award_amount": contract_fields["award_amount"],
        "expiration_date": expiration,
        "expected_repost_start": repost_start,
        "expected_repost_end": repost_end,
        "priority": WatchlistPriority(priority_label),
    }


def upsert_watchlist(db: Session, contract_fields: dict, *, commit: bool = True) -> int:
    fields = build_watchlist_fields(contract_fields)
    existing = (
        db.query(Watchlist)
        .filter(Watchlist.award_id == fields["award_id"])
        .one_or_none()
    )
    now = datetime.utcnow()

    if existing:
        preserved_status = existing.status
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.status = preserved_status
        existing.updated_at = now
        if commit:
            db.commit()
        return 1

    entry = Watchlist(**fields, status=WatchlistStatus.WATCHING, created_at=now, updated_at=now)
    db.add(entry)
    if commit:
        db.commit()
    return 1


def remove_stale_watchlist(db: Session, window_start: date) -> int:
    stale = db.query(Watchlist).filter(Watchlist.expiration_date < window_start).all()
    for entry in stale:
        db.delete(entry)
    db.commit()
    return len(stale)


def remove_out_of_window_watchlist(db: Session, window_end: date) -> int:
    outside = db.query(Watchlist).filter(Watchlist.expiration_date > window_end).all()
    for entry in outside:
        db.delete(entry)
    db.commit()
    return len(outside)
