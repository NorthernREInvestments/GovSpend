import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
from app.services.app_settings import get_or_create_app_settings
from app.services.dashboard_data import build_dashboard_data
from app.services.scoring import (
    compute_recurring_profile,
    contract_total_obligation,
    effective_annual_value,
    evaluate_contract_pursuit,
    format_period_years,
    recurrence_pattern_class,
    usaspending_award_url,
)
from app.database import SessionLocal, get_db
from app.models import Contract, ContractStatus, SyncLog
from app.routers import contracts
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


MOUNTAIN_TZ = ZoneInfo("America/Denver")


def _mountain_time_display(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    local = value.astimezone(MOUNTAIN_TZ)
    tz_label = local.tzname() or "MT"
    return f"{local.strftime('%b %d, %Y %I:%M %p')} {tz_label}"


def _format_currency(value: float) -> str:
    return f"${value:,.0f}"


def _days_until(expiration: date) -> int:
    return (expiration - date.today()).days


def _contract_annual(contract: Contract) -> float:
    return effective_annual_value(
        estimated_annual_value=contract.estimated_annual_value,
        total_obligation=contract.total_obligation,
        award_amount=contract.award_amount,
        start_date=contract.start_date,
        expiration_date=contract.expiration_date,
    )


def _contract_total(contract: Contract) -> float:
    return contract_total_obligation(
        total_obligation=contract.total_obligation,
        award_amount=contract.award_amount,
    )


def _contract_pursuit_eval(contract: Contract):
    return evaluate_contract_pursuit(contract)


def _contract_priority_tier(contract: Contract) -> str:
    return _contract_pursuit_eval(contract).priority_tier


def _contract_pursuit_score_display(contract: Contract) -> int:
    return _contract_pursuit_eval(contract).pursuit_score


def _contract_expires_display(contract: Contract) -> str:
    days = _contract_pursuit_eval(contract).days_until_expiration
    if days < 0:
        return f"Expired {abs(days)} days ago"
    if days == 0:
        return "Expires today"
    return f"Expires in {days} days"


def _contract_bidders_display(contract: Contract) -> str:
    return _contract_pursuit_eval(contract).bidders_display


def _contract_length_summary(contract: Contract) -> str:
    profile = _contract_recurring_profile(contract)
    parts = []
    if profile["period_years"] is not None:
        parts.append(f"Period: {format_period_years(profile['period_years'])}")
    if profile["remaining_option_years"]:
        parts.append(
            f"Options left: {format_period_years(profile['remaining_option_years'])}"
        )
    elif profile["total_runway_years"]:
        parts.append(f"Runway: {format_period_years(profile['total_runway_years'])}")
    return " · ".join(parts)


def _contract_recurring_profile(contract: Contract) -> dict:
    if contract.recurring_fit:
        return {
            "period_years": contract.period_years,
            "remaining_option_years": contract.remaining_option_years,
            "total_runway_years": contract.total_runway_years,
            "recurring_fit": contract.recurring_fit,
            "recurring_fit_score": contract.recurring_fit_score,
        }
    return compute_recurring_profile(
        start_date=contract.start_date,
        expiration_date=contract.expiration_date,
        potential_end_date=contract.potential_end_date,
        pop_flag=contract.pop_flag,
    )


def _recurring_fit_class(contract: Contract) -> str:
    fit = _contract_recurring_profile(contract)["recurring_fit"]
    if fit.startswith("Ideal"):
        return "ideal"
    if fit.startswith("Strong"):
        return "strong"
    if fit == "Annual recompete" or fit == "Annual period":
        return "annual"
    if fit == "Short period":
        return "short"
    return "standard"


templates.env.filters["currency"] = _format_currency
templates.env.filters["mountain_time"] = _mountain_time_display
templates.env.filters["days_until"] = _days_until
templates.env.filters["priority_tier"] = _contract_priority_tier
templates.env.filters["contract_annual"] = _contract_annual
templates.env.filters["contract_total"] = _contract_total
templates.env.filters["contract_length_summary"] = _contract_length_summary
templates.env.filters["recurring_fit_class"] = _recurring_fit_class
templates.env.filters["pursuit_score_display"] = _contract_pursuit_score_display
templates.env.filters["expires_display"] = _contract_expires_display
templates.env.filters["bidders_display"] = _contract_bidders_display
templates.env.filters["recurrence_pattern_class"] = recurrence_pattern_class
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
            "asset_version": settings.static_asset_version,
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
