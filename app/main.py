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
from app.services.dashboard_data import build_dashboard_data
from app.services.scoring import priority_tier, usaspending_award_url
from app.database import SessionLocal, get_db
from app.models import Contract, ContractStatus, SyncLog
from app.routers import contracts
from app.services.app_settings import get_or_create_app_settings
from app.services.cleanup import CleanupService
from app.services.sync import ContractSyncService
from app.services.sync_lock import SyncInProgressError, clear_orphaned_running_syncs, is_sync_running

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

        db = SessionLocal()
        try:
            cleared = clear_orphaned_running_syncs(db)
            if cleared:
                logger.info("Cleared %s orphaned sync(s) from previous deploy", cleared)
        finally:
            db.close()

        if settings.sync_on_startup:
            db = SessionLocal()
            try:
                from app.services.sync_lock import is_sync_running

                if is_sync_running(db):
                    logger.info("Skipping startup sync — one is already running")
                    return
                logger.info("Running startup contract sync in background")
                await ContractSyncService(db).run_sync()
            except SyncInProgressError:
                logger.info("Startup sync skipped — another sync is active")
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
    data = build_dashboard_data(db)
    latest_sync = (
        db.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
    )
    sync_running = is_sync_running(db)
    app_settings = get_or_create_app_settings(db)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "contracts": data["contracts"],
            "hot_leads": data["hot_leads"],
            "app_settings": app_settings,
            "stats": data["stats"].model_dump(),
            "statuses": [status.value for status in ContractStatus],
            "latest_sync": latest_sync,
            "sync_running": sync_running,
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
