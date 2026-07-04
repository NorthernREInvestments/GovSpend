from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Contract
from app.services.app_settings import get_or_create_app_settings
from app.services.scoring import (
    annual_value_in_range,
    fields_have_option_year_structure,
)
from app.services.usaspending import USAspendingClient, parse_end_date


def _browse_fit_note(
    *,
    in_pursuit_range: bool,
    has_option_years: bool,
    in_pipeline: bool,
) -> str:
    if in_pipeline:
        return "In your pipeline"
    if in_pursuit_range and has_option_years:
        return "Matches pursuit filters"
    if not has_option_years:
        return "No option-year structure"
    if not in_pursuit_range:
        return "Outside pursuit $ range"
    return "Market snapshot"


def _browse_sort_key(item: dict) -> tuple:
    return (
        0 if item["in_pipeline"] else 1,
        0 if item["in_pursuit_range"] and item["has_option_years"] else 1,
        item["expiration_date"],
    )


async def fetch_market_browse(db: Session) -> dict:
    app_settings = get_or_create_app_settings(db)
    pursuit_min = app_settings.min_award_amount
    pursuit_max = app_settings.max_award_amount or 350_000
    window_start = date.today()
    window_end = window_start + timedelta(days=app_settings.expiration_days)

    client = USAspendingClient(
        min_award_amount=1_000,
        max_award_amount=None,
        max_annual_value=settings.browse_max_annual,
    )
    client.min_annual_value = settings.browse_min_annual

    raw_rows: list[dict] = []
    pages_scanned = 0
    async for page_batch, pages, _naics in client.stream_expiring_contracts(
        window_start,
        window_end,
        max_pages_per_naics=settings.browse_max_pages_per_naics,
    ):
        pages_scanned = pages
        raw_rows.extend(page_batch)

    raw_rows.sort(
        key=lambda row: parse_end_date(row.get("End Date")) or date.max,
    )
    candidates = raw_rows[: settings.browse_max_results * 2]
    enriched = await client.enrich_awards_batch(
        candidates,
        concurrency=8,
        include_prior_search=False,
    )

    pipeline_ids = {
        award_id
        for (award_id,) in db.query(Contract.award_id).all()
        if award_id
    }

    results: list[dict] = []
    for fields in enriched:
        annual = fields.get("estimated_annual_value") or 0.0
        if not annual_value_in_range(
            annual,
            settings.browse_min_annual,
            settings.browse_max_annual,
        ):
            continue

        in_pursuit_range = annual_value_in_range(annual, pursuit_min, pursuit_max)
        has_option_years = fields_have_option_year_structure(fields)
        in_pipeline = fields["award_id"] in pipeline_ids

        results.append(
            {
                "award_id": fields["award_id"],
                "generated_internal_id": fields.get("generated_internal_id"),
                "contract_name": fields["contract_name"],
                "naics_code": fields["naics_code"],
                "agency": fields["agency"],
                "incumbent_name": fields["incumbent_name"],
                "place_of_performance": fields["place_of_performance"],
                "expiration_date": fields["expiration_date"],
                "potential_end_date": fields.get("potential_end_date"),
                "estimated_annual_value": annual,
                "total_obligation": fields.get("total_obligation") or fields["award_amount"],
                "pop_flag": fields.get("pop_flag", ""),
                "period_years": fields.get("period_years"),
                "remaining_option_years": fields.get("remaining_option_years"),
                "in_pursuit_range": in_pursuit_range,
                "has_option_years": has_option_years,
                "in_pipeline": in_pipeline,
                "fit_note": _browse_fit_note(
                    in_pursuit_range=in_pursuit_range,
                    has_option_years=has_option_years,
                    in_pipeline=in_pipeline,
                ),
            }
        )

    results.sort(key=_browse_sort_key)
    results = results[: settings.browse_max_results]

    return {
        "window_start": window_start,
        "window_end": window_end,
        "pursuit_min_annual": pursuit_min,
        "pursuit_max_annual": pursuit_max,
        "browse_min_annual": settings.browse_min_annual,
        "browse_max_annual": settings.browse_max_annual,
        "pages_scanned": pages_scanned,
        "candidates_scanned": len(raw_rows),
        "results": results,
    }
