from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Contract, ContractStatus
from app.schemas import DashboardStats
from app.services.app_settings import get_or_create_app_settings
from app.services.scoring import (
    apply_option_years_filter,
    apply_recompete_filter,
    effective_annual_value,
    evaluate_contract_pursuit,
    pursuit_score_for_contract,
)


def pursuit_contracts_query(db: Session):
    return db.query(Contract)


def _annual_bounds(db: Session) -> tuple[float, float]:
    settings = get_or_create_app_settings(db)
    return (
        settings.min_award_amount,
        settings.max_award_amount or 350_000,
    )


def contract_pursuit_score(contract: Contract, db: Session) -> float:
    min_annual, max_annual = _annual_bounds(db)
    return pursuit_score_for_contract(
        estimated_annual_value=contract.estimated_annual_value,
        total_obligation=contract.total_obligation,
        award_amount=contract.award_amount,
        start_date=contract.start_date,
        expiration_date=contract.expiration_date,
        potential_end_date=contract.potential_end_date,
        pop_flag=contract.pop_flag,
        recurrence_pattern=contract.recurrence_pattern or "",
        remaining_option_years=contract.remaining_option_years,
        number_of_offers_received=contract.number_of_offers_received,
        set_aside=contract.set_aside,
        min_annual_value=min_annual,
        max_annual_value=max_annual,
    )


def sort_pursuit_contracts(contracts: list[Contract], db: Session) -> list[Contract]:
    return sorted(
        contracts,
        key=lambda contract: (
            -contract_pursuit_score(contract, db),
            contract.expiration_date,
            -contract.estimated_annual_value,
        ),
    )


def build_dashboard_data(db: Session) -> dict:
    settings = get_or_create_app_settings(db)
    min_annual = settings.min_award_amount
    max_annual = settings.max_award_amount or 350_000

    all_contracts = sort_pursuit_contracts(pursuit_contracts_query(db).all(), db)
    actionable = [
        contract
        for contract in all_contracts
        if contract.status not in (ContractStatus.WON, ContractStatus.LOST)
    ]
    contract_rows = actionable or all_contracts
    contract_rows = apply_option_years_filter(contract_rows)
    contract_rows = apply_recompete_filter(
        contract_rows,
        recompete_only=settings.recompete_only,
    )

    hot_leads = [
        contract
        for contract in contract_rows
        if evaluate_contract_pursuit(
            contract,
            min_annual_value=min_annual,
            max_annual_value=max_annual,
        ).priority_tier
        == "High"
    ][:5]

    today = date.today()
    if today.month == 12:
        month_end = date(today.year, 12, 31)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    total_value = sum(
        effective_annual_value(
            estimated_annual_value=contract.estimated_annual_value,
            total_obligation=contract.total_obligation,
            award_amount=contract.award_amount,
            start_date=contract.start_date,
            expiration_date=contract.expiration_date,
        )
        for contract in contract_rows
    )
    expiring_this_month = sum(
        1
        for contract in contract_rows
        if today <= contract.expiration_date <= month_end
    )
    by_status = {status.value: 0 for status in ContractStatus}
    for contract in contract_rows:
        by_status[contract.status.value] += 1

    stats = DashboardStats(
        total_contracts=len(contract_rows),
        total_value=total_value,
        expiring_this_month=expiring_this_month,
        by_status=by_status,
    )

    return {
        "contracts": contract_rows,
        "hot_leads": hot_leads,
        "stats": stats,
    }
