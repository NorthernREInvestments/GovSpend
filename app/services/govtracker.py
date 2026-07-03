import logging
import re
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Watchlist, WatchlistStatus
from app.schemas import WatchlistMatchNotification

logger = logging.getLogger(__name__)


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _normalize_state(value: str) -> str:
    cleaned = value.strip().upper()
    if len(cleaned) == 2:
        return cleaned
    return _normalize_text(value)


def _states_match(stored: str, incoming: str) -> bool:
    if _normalize_text(stored) == _normalize_text(incoming):
        return True
    return _normalize_state(stored) == _normalize_state(incoming)


def _normalize_naics(value: str) -> str:
    digits = re.sub(r"\D", "", value.strip())
    if len(digits) < 6:
        raise ValueError("NAICS code must contain at least 6 digits")
    return digits[:6]


def find_watchlist_matches(db: Session, payload: WatchlistMatchNotification) -> list[Watchlist]:
    agency = _normalize_text(payload.agency)
    city = _normalize_text(payload.location_city)
    naics = _normalize_naics(payload.naics_code)

    candidates = (
        db.query(Watchlist)
        .filter(func.lower(func.trim(Watchlist.naics_code)) == naics)
        .all()
    )

    matches: list[Watchlist] = []
    for row in candidates:
        if _normalize_text(row.agency) != agency:
            continue
        if _normalize_text(row.location_city) != city:
            continue
        if not _states_match(row.location_state, payload.location_state):
            continue
        matches.append(row)

    return matches


def apply_govtracker_match(db: Session, payload: WatchlistMatchNotification) -> list[Watchlist]:
    matches = find_watchlist_matches(db, payload)
    if not matches:
        return []

    now = datetime.utcnow()
    updated: list[Watchlist] = []

    for entry in matches:
        if entry.status in (WatchlistStatus.WON, WatchlistStatus.LOST):
            continue
        if entry.status != WatchlistStatus.FOUND_ON_SAM:
            entry.status = WatchlistStatus.FOUND_ON_SAM
            entry.updated_at = now
        updated.append(entry)

    if updated:
        db.commit()
        for entry in updated:
            db.refresh(entry)

    extra = ""
    if payload.sam_notice_id:
        extra = f" sam_notice_id={payload.sam_notice_id}"
    logger.info(
        "GovTracker match: agency=%s city=%s state=%s naics=%s — updated %s row(s)%s",
        payload.agency,
        payload.location_city,
        payload.location_state,
        payload.naics_code,
        len(updated),
        extra,
    )
    return updated
