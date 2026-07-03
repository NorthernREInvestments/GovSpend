import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import settings
from app.services.scoring import compute_pursuit_score

logger = logging.getLogger(__name__)

SEARCH_FIELDS = [
    "Award ID",
    "Description",
    "Recipient Name",
    "Award Amount",
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


class USAspendingClient:
    def __init__(
        self,
        min_award_amount: float | None = None,
        max_award_amount: float | None = None,
    ) -> None:
        self.base_url = settings.usaspending_base_url.rstrip("/")
        self.min_award_amount = (
            min_award_amount if min_award_amount is not None else settings.min_award_amount
        )
        self.max_award_amount = (
            max_award_amount if max_award_amount is not None else settings.max_award_amount
        )

    def _award_amount_filter(self) -> list[dict[str, float]]:
        amount_filter: dict[str, float] = {"lower_bound": self.min_award_amount}
        if self.max_award_amount is not None:
            amount_filter["upper_bound"] = self.max_award_amount
        return [amount_filter]

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
                    award_amount = float(row.get("Award Amount") or 0)
                    if (
                        self.max_award_amount is not None
                        and award_amount > self.max_award_amount
                    ):
                        continue
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

    async def enrich_award(self, generated_internal_id: str) -> dict[str, str]:
        empty = {
            "contracting_office": "",
            "co_name": "",
            "set_aside": "",
            "extent_competed": "",
            "solicitation_number": "",
        }
        if not generated_internal_id:
            return empty

        async with httpx.AsyncClient(timeout=60.0) as client:
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
            co_name = await self._fetch_contracting_officer(
                client,
                award_id=data.get("piid") or data.get("generated_unique_award_id"),
            )

            if not contracting_office:
                subtier = awarding_agency.get("subtier_agency") or {}
                contracting_office = subtier.get("name") or ""

            tx = data.get("latest_transaction_contract_data") or {}
            return {
                "contracting_office": contracting_office,
                "co_name": co_name,
                "set_aside": tx.get("type_set_aside_description") or tx.get("type_set_aside") or "",
                "extent_competed": tx.get("extent_competed_description") or tx.get("extent_competed") or "",
                "solicitation_number": tx.get("solicitation_identifier") or "",
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


def map_award_to_contract_fields(row: dict[str, Any], enrichment: dict[str, str]) -> dict[str, Any]:
    description = (row.get("Description") or "").strip()
    award_id = row.get("Award ID") or ""
    end_date = parse_end_date(row.get("End Date"))
    if end_date is None:
        raise ValueError(f"Missing end date for award {award_id}")

    subtier = row.get("Awarding Sub Agency") or ""
    contracting_office = enrichment.get("contracting_office") or subtier
    award_amount = float(row.get("Award Amount") or 0)
    pop = row.get("Primary Place of Performance")
    location_city, location_state = parse_location(pop)

    return {
        "award_id": award_id,
        "generated_internal_id": row.get("generated_internal_id"),
        "contract_name": description or award_id,
        "award_amount": award_amount,
        "agency": row.get("Awarding Agency") or "Unknown Agency",
        "place_of_performance": format_place_of_performance(pop),
        "location_city": location_city,
        "location_state": location_state,
        "incumbent_name": row.get("Recipient Name") or "Unknown",
        "expiration_date": end_date,
        "contracting_office": contracting_office,
        "co_name": enrichment.get("co_name") or "",
        "naics_code": parse_naics_code(row.get("NAICS")),
        "set_aside": enrichment.get("set_aside") or "",
        "extent_competed": enrichment.get("extent_competed") or "",
        "solicitation_number": enrichment.get("solicitation_number") or "",
        "pursuit_score": compute_pursuit_score(award_amount, end_date),
    }
