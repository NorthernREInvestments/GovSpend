import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import settings
from app.services.scoring import (
    compute_estimated_annual_value,
    compute_pop_flag,
    compute_pursuit_score,
    compute_recurring_profile,
)

logger = logging.getLogger(__name__)

SEARCH_FIELDS = [
    "Award ID",
    "Description",
    "Recipient Name",
    "Award Amount",
    "Start Date",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Primary Place of Performance",
    "End Date",
    "NAICS",
    "generated_internal_id",
]


def format_place_of_performance(pop: Any) -> str:
    if not pop:
        return ""
    if isinstance(pop, str):
        return pop
    parts = [
        pop.get("city_name"),
        pop.get("state_name") or pop.get("state_code"),
        pop.get("country_name") or pop.get("country_code"),
    ]
    return ", ".join(part for part in parts if part)


def parse_location(pop: Any) -> tuple[str, str]:
    if not pop or isinstance(pop, str):
        return "", ""
    city = (pop.get("city_name") or "").strip()
    state = (pop.get("state_name") or pop.get("state_code") or "").strip()
    return city, state


def parse_naics_code(naics: Any) -> str:
    if not naics:
        return ""
    if isinstance(naics, dict):
        return str(naics.get("code", ""))
    return str(naics)


def parse_end_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


class USAspendingClient:
    def __init__(
        self,
        min_award_amount: float | None = None,
        max_award_amount: float | None = None,
        max_annual_value: float | None = None,
    ) -> None:
        self.base_url = settings.usaspending_base_url.rstrip("/")
        self.min_award_amount = (
            min_award_amount if min_award_amount is not None else settings.min_award_amount
        )
        self.max_award_amount = (
            max_award_amount if max_award_amount is not None else settings.max_award_amount
        )
        self.max_annual_value = (
            max_annual_value
            if max_annual_value is not None
            else (self.max_award_amount or 350_000)
        )

    def _award_amount_filter(self) -> list[dict[str, float]]:
        # Loose total-value floor; annual range is enforced after detail enrichment.
        return [{"lower_bound": max(1_000, self.min_award_amount)}]

    async def stream_expiring_contracts(
        self,
        window_start: date,
        window_end: date,
        seen_award_ids: set[str] | None = None,
    ) -> AsyncIterator[tuple[list[dict[str, Any]], int, str]]:
        """Yield in-window awards after each API page across parallel NAICS scans."""
        seen = seen_award_ids if seen_award_ids is not None else set()
        dedup_lock = asyncio.Lock()
        total_pages = 0
        queue: asyncio.Queue[tuple[str, list[dict[str, Any]]] | None] = asyncio.Queue()
        naics_codes = settings.naics_codes
        parallel_limit = min(settings.naics_parallel_limit, len(naics_codes))
        semaphore = asyncio.Semaphore(parallel_limit)

        logger.info(
            "Starting parallel USAspending search across %s NAICS codes (up to %s at once)",
            len(naics_codes),
            parallel_limit,
        )

        async def scan_naics(client: httpx.AsyncClient, naics_code: str) -> None:
            async with semaphore:
                try:
                    async for page_batch in self._stream_naics_pages(
                        client=client,
                        naics_code=naics_code,
                        window_start=window_start,
                        window_end=window_end,
                        seen_award_ids=seen,
                        dedup_lock=dedup_lock,
                    ):
                        await queue.put((naics_code, page_batch))
                finally:
                    await queue.put(None)

        async with httpx.AsyncClient(timeout=120.0) as client:
            tasks = [
                asyncio.create_task(scan_naics(client, naics_code))
                for naics_code in naics_codes
            ]

            finished = 0
            try:
                while finished < len(naics_codes):
                    item = await queue.get()
                    if item is None:
                        finished += 1
                        continue
                    naics_code, page_batch = item
                    total_pages += 1
                    yield page_batch, total_pages, naics_code
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _stream_naics_pages(
        self,
        client: httpx.AsyncClient,
        naics_code: str,
        window_start: date,
        window_end: date,
        seen_award_ids: set[str],
        dedup_lock: asyncio.Lock | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1
        has_next = True
        naics_found = 0
        mod_start = date.today() - timedelta(days=730)

        while has_next and page <= settings.max_pages_per_sync:
            if page == 1:
                logger.info("Starting USAspending search for NAICS %s", naics_code)

            payload = {
                "filters": {
                    "award_type_codes": ["A", "B", "C", "D"],
                    "naics_codes": {"require": [naics_code]},
                    "award_amounts": self._award_amount_filter(),
                    "time_period": [
                        {
                            "start_date": mod_start.isoformat(),
                            "end_date": window_end.isoformat(),
                            "date_type": "last_modified_date",
                        }
                    ],
                },
                "fields": SEARCH_FIELDS,
                "limit": settings.api_page_limit,
                "page": page,
                "sort": "End Date",
                "order": "asc",
            }

            response = await client.post(
                f"{self.base_url}/api/v2/search/spending_by_award/",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            page_results = data.get("results", [])
            if not page_results:
                break

            page_end_dates = [
                parsed
                for row in page_results
                if (parsed := parse_end_date(row.get("End Date"))) is not None
            ]
            if page_end_dates and min(page_end_dates) > window_end:
                break

            page_batch: list[dict[str, Any]] = []
            for row in page_results:
                end_date = parse_end_date(row.get("End Date"))
                if end_date is None:
                    continue
                if window_start <= end_date <= window_end:
                    award_id = row.get("Award ID")
                    if not award_id:
                        continue
                    if dedup_lock is not None:
                        async with dedup_lock:
                            if award_id in seen_award_ids:
                                continue
                            seen_award_ids.add(award_id)
                    elif award_id not in seen_award_ids:
                        seen_award_ids.add(award_id)
                    else:
                        continue
                    page_batch.append(row)
                    naics_found += 1

            has_next = bool(data.get("page_metadata", {}).get("hasNext"))
            yield page_batch
            page += 1
            await asyncio.sleep(settings.api_request_delay_seconds)

        logger.info(
            "NAICS %s: scanned %s pages, found %s contracts in window",
            naics_code,
            page - 1,
            naics_found,
        )

    async def search_expiring_contracts(
        self,
        window_start: date,
        window_end: date,
    ) -> tuple[list[dict[str, Any]], int]:
        results: list[dict[str, Any]] = []
        seen_award_ids: set[str] = set()
        total_pages = 0

        async for page_batch, pages, _naics in self.stream_expiring_contracts(
            window_start, window_end, seen_award_ids
        ):
            total_pages = pages
            results.extend(page_batch)

        return results, total_pages

    async def enrich_awards_batch(
        self,
        awards: list[dict[str, Any]],
        *,
        concurrency: int = 6,
    ) -> list[dict[str, Any]]:
        if not awards:
            return []

        semaphore = asyncio.Semaphore(concurrency)
        shared_client = httpx.AsyncClient(timeout=60.0)

        async def enrich_one(row: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                generated_internal_id = row.get("generated_internal_id")
                enrichment = await self._enrich_award_with_client(
                    shared_client,
                    generated_internal_id,
                    include_co=False,
                )
                return map_award_to_contract_fields(
                    row,
                    enrichment,
                    max_annual_value=self.max_annual_value,
                )

        try:
            return await asyncio.gather(*(enrich_one(row) for row in awards))
        finally:
            await shared_client.aclose()

    async def enrich_award(
        self,
        generated_internal_id: str,
        *,
        include_co: bool = True,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._enrich_award_with_client(
                client,
                generated_internal_id,
                include_co=include_co,
            )

    async def _enrich_award_with_client(
        self,
        client: httpx.AsyncClient,
        generated_internal_id: str | None,
        *,
        include_co: bool,
    ) -> dict[str, Any]:
        empty: dict[str, Any] = {
            "contracting_office": "",
            "co_name": "",
            "set_aside": "",
            "extent_competed": "",
            "solicitation_number": "",
            "start_date": None,
            "total_obligation": None,
            "base_exercised_options_value": None,
            "base_all_options_value": None,
            "potential_end_date": None,
            "number_of_offers_received": None,
        }
        if not generated_internal_id:
            return empty

        try:
            response = await client.get(
                f"{self.base_url}/api/v2/awards/{generated_internal_id}/"
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            logger.warning("Failed to enrich award %s", generated_internal_id)
            return empty

        awarding_agency = data.get("awarding_agency") or {}
        contracting_office = awarding_agency.get("office_agency_name") or ""
        co_name = ""
        if include_co:
            co_name = await self._fetch_contracting_officer(
                client,
                award_id=data.get("piid") or data.get("generated_unique_award_id"),
            )

        if not contracting_office:
            subtier = awarding_agency.get("subtier_agency") or {}
            contracting_office = subtier.get("name") or ""

        tx = data.get("latest_transaction_contract_data") or {}
        pop = data.get("period_of_performance") or {}
        start_date = parse_end_date(pop.get("start_date")) or parse_end_date(
            data.get("period_of_performance_start_date")
        )

        def _float_or_none(value: Any) -> float | None:
            if value in (None, ""):
                return None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed > 0 else None

        return {
            "contracting_office": contracting_office,
            "co_name": co_name,
            "set_aside": tx.get("type_set_aside_description") or tx.get("type_set_aside") or "",
            "extent_competed": tx.get("extent_competed_description") or tx.get("extent_competed") or "",
            "solicitation_number": tx.get("solicitation_identifier") or "",
            "start_date": start_date,
            "total_obligation": _float_or_none(data.get("total_obligation")),
            "base_exercised_options_value": _float_or_none(data.get("base_exercised_options")),
            "base_all_options_value": _float_or_none(data.get("base_and_all_options")),
            "potential_end_date": parse_end_date(pop.get("potential_end_date")),
            "number_of_offers_received": parse_int_or_none(tx.get("number_of_offers_received")),
        }

    async def _fetch_contracting_officer(self, client: httpx.AsyncClient, award_id: Any) -> str:
        if not award_id:
            return ""

        try:
            response = await client.post(
                f"{self.base_url}/api/v2/search/spending_by_transaction/",
                json={
                    "filters": {
                        "award_type_codes": ["A", "B", "C", "D"],
                        "award_ids": [str(award_id)],
                    },
                    "fields": ["Action Date", "Contracting Officer's Name"],
                    "page": 1,
                    "limit": 1,
                    "sort": "Action Date",
                    "order": "desc",
                },
            )
            if response.status_code != 200:
                return ""
            results = response.json().get("results", [])
            if not results:
                return ""
            return (
                results[0].get("Contracting Officer's Name")
                or results[0].get("contracting_officers_name")
                or ""
            )
        except httpx.HTTPError:
            return ""


def contract_to_award_row(contract: Any) -> dict[str, Any]:
    return {
        "Award ID": contract.award_id,
        "Description": contract.contract_name,
        "Recipient Name": contract.incumbent_name,
        "Award Amount": contract.award_amount,
        "Start Date": contract.start_date.isoformat() if contract.start_date else "",
        "Awarding Agency": contract.agency,
        "Awarding Sub Agency": contract.contracting_office,
        "Primary Place of Performance": contract.place_of_performance,
        "End Date": contract.expiration_date.isoformat(),
        "NAICS": contract.naics_code,
        "generated_internal_id": contract.generated_internal_id,
    }


def map_award_to_contract_fields(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    *,
    max_annual_value: float = 350_000,
) -> dict[str, Any]:
    description = (row.get("Description") or "").strip()
    award_id = row.get("Award ID") or ""
    end_date = parse_end_date(row.get("End Date"))
    if end_date is None:
        raise ValueError(f"Missing end date for award {award_id}")

    start_date = enrichment.get("start_date") or parse_end_date(row.get("Start Date"))
    total_obligation = enrichment.get("total_obligation")
    if total_obligation is None:
        total_obligation = float(row.get("Award Amount") or 0)
    else:
        total_obligation = float(total_obligation)

    base_exercised = enrichment.get("base_exercised_options_value")
    base_all = enrichment.get("base_all_options_value")
    potential_end_date = enrichment.get("potential_end_date")
    pop_flag = compute_pop_flag(base_exercised, base_all)
    recurring = compute_recurring_profile(
        start_date=start_date,
        expiration_date=end_date,
        potential_end_date=potential_end_date,
        pop_flag=pop_flag,
    )
    estimated_annual = compute_estimated_annual_value(total_obligation, start_date, end_date)
    if estimated_annual is None:
        estimated_annual = 0.0

    subtier = row.get("Awarding Sub Agency") or ""
    contracting_office = enrichment.get("contracting_office") or subtier
    pop = row.get("Primary Place of Performance")
    location_city, location_state = parse_location(pop)

    return {
        "award_id": award_id,
        "generated_internal_id": row.get("generated_internal_id"),
        "contract_name": description or award_id,
        "award_amount": total_obligation,
        "start_date": start_date,
        "total_obligation": total_obligation,
        "base_exercised_options_value": base_exercised,
        "base_all_options_value": base_all,
        "estimated_annual_value": estimated_annual,
        "pop_flag": pop_flag,
        "period_years": recurring["period_years"],
        "remaining_option_years": recurring["remaining_option_years"],
        "total_runway_years": recurring["total_runway_years"],
        "recurring_fit": recurring["recurring_fit"],
        "recurring_fit_score": recurring["recurring_fit_score"],
        "agency": row.get("Awarding Agency") or "Unknown Agency",
        "place_of_performance": format_place_of_performance(pop),
        "location_city": location_city,
        "location_state": location_state,
        "incumbent_name": row.get("Recipient Name") or "Unknown",
        "expiration_date": end_date,
        "potential_end_date": potential_end_date,
        "number_of_offers_received": enrichment.get("number_of_offers_received"),
        "contracting_office": contracting_office,
        "co_name": enrichment.get("co_name") or "",
        "naics_code": parse_naics_code(row.get("NAICS")),
        "set_aside": enrichment.get("set_aside") or "",
        "extent_competed": enrichment.get("extent_competed") or "",
        "solicitation_number": enrichment.get("solicitation_number") or "",
        "pursuit_score": compute_pursuit_score(
            estimated_annual,
            end_date,
            max_annual_value=max_annual_value,
            recurring_fit_score=float(recurring["recurring_fit_score"] or 0.4),
        ),
    }
