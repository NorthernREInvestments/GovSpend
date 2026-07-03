import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, Request
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.startup import init_database, is_db_ready
from app.services.scoring import priority_tier, usaspending_award_url
from app.database import SessionLocal, get_db
from app.models import Contract, ContractStatus, SyncLog
from app.routers import contracts
from app.services.app_settings import get_or_create_app_settings
from app.services.cleanup import CleanupService
from app.services.sync import ContractSyncService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def run_scheduled_sync() -> None:
    db = SessionLocal()
    try:
        import asyncio

        asyncio.run(ContractSyncService(db).run_sync())
    except Exception:
        logger.exception("Scheduled sync failed")
    finally:
        db.close()


def run_scheduled_cleanup() -> None:
    db = SessionLocal()
    try:
        CleanupService(db).run_cleanup()
    except Exception:
        logger.exception("Scheduled cleanup failed")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_scheduled_sync,
        CronTrigger(hour=settings.refresh_hour, minute=settings.refresh_minute),
        id="daily_contract_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_cleanup,
        CronTrigger(hour=settings.cleanup_hour, minute=settings.cleanup_minute),
        id="daily_contract_cleanup",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — sync at %02d:%02d UTC, cleanup at %02d:%02d UTC",
        settings.refresh_hour,
        settings.refresh_minute,
        settings.cleanup_hour,
        settings.cleanup_minute,
    )

    async def _init_and_maybe_sync():
        ready = await asyncio.to_thread(init_database)
        if not ready:
            return
        if settings.sync_on_startup:
            db = SessionLocal()
            try:
                latest = ContractSyncService(db).get_latest_sync_log()
                if latest is None or latest.status != "running":
                    logger.info("Running startup contract sync in background")
                    await ContractSyncService(db).run_sync()
            except Exception:
                logger.exception("Startup sync failed")
            finally:
                db.close()

    asyncio.create_task(_init_and_maybe_sync())

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(title="GovSpend", description="Government contract expiration tracker", lifespan=lifespan)
app.include_router(contracts.router)

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _format_currency(value: float) -> str:
    return f"${value:,.0f}"


def _days_until(expiration: date) -> int:
    return (expiration - date.today()).days


templates.env.filters["currency"] = _format_currency
templates.env.filters["days_until"] = _days_until
templates.env.filters["priority_tier"] = priority_tier
templates.env.filters["award_url"] = usaspending_award_url


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    all_contracts = (
        db.query(Contract)
        .order_by(Contract.pursuit_score.desc(), Contract.expiration_date.asc())
        .all()
    )
    actionable = [
        c for c in all_contracts
        if c.status not in (ContractStatus.WON, ContractStatus.LOST)
    ]
    contract_rows = actionable or all_contracts
    hot_leads = [c for c in contract_rows if priority_tier(c.award_amount, c.expiration_date) == "High"][:5]

    today = date.today()
    if today.month == 12:
        month_end = date(today.year, 12, 31)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    total_value = sum(c.award_amount for c in contract_rows)
    expiring_this_month = sum(
        1 for c in contract_rows if today <= c.expiration_date <= month_end
    )
    by_status = {status.value: 0 for status in ContractStatus}
    for contract in contract_rows:
        by_status[contract.status.value] += 1

    latest_sync = (
        db.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
    )
    app_settings = get_or_create_app_settings(db)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "contracts": contract_rows,
            "hot_leads": hot_leads,
            "app_settings": app_settings,
            "stats": {
                "total_contracts": len(contract_rows),
                "total_value": total_value,
                "expiring_this_month": expiring_this_month,
                "by_status": by_status,
            },
            "statuses": [status.value for status in ContractStatus],
            "latest_sync": latest_sync,
            "now": datetime.utcnow(),
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    if is_db_ready():
        return {"status": "ready", "database": "connected"}
    return JSONResponse(
        status_code=503,
        content={
            "status": "starting",
            "database": "unavailable",
            "hint": "Add PostgreSQL on Railway and ensure DATABASE_URL is set",
        },
    )
