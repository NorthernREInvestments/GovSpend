from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Contract, ContractStatus
from app.schemas import DashboardStats
from app.services.app_settings import get_or_create_app_settings
from app.services.scoring import (
    effective_annual_value,
    priority_tier,
    pursuit_score_for_contract,
)


def pursuit_contracts_query(db: Session):
    return db.query(Contract)


def sort_pursuit_contracts(contracts: list[Contract], db: Session) -> list[Contract]:
    settings = get_or_create_app_settings(db)
    max_annual = settings.max_award_amount or 350_000
    return sorted(
        contracts,
        key=lambda contract: (
            -pursuit_score_for_contract(
                estimated_annual_value=contract.estimated_annual_value,
                total_obligation=contract.total_obligation,
                award_amount=contract.award_amount,
                start_date=contract.start_date,
                expiration_date=contract.expiration_date,
                potential_end_date=contract.potential_end_date,
                pop_flag=contract.pop_flag,
                recurring_fit_score=contract.recurring_fit_score or None,
                max_annual_value=max_annual,
            ),
            -(contract.recurring_fit_score or 0),
            contract.expiration_date,
            -contract.estimated_annual_value,
            -contract.award_amount,
        ),
    )


def build_dashboard_data(db: Session) -> dict:
    all_contracts = sort_pursuit_contracts(pursuit_contracts_query(db).all(), db)
    actionable = [
        contract
        for contract in all_contracts
        if contract.status not in (ContractStatus.WON, ContractStatus.LOST)
    ]
    contract_rows = actionable or all_contracts
    hot_leads = [
        contract
        for contract in contract_rows
        if priority_tier(
            effective_annual_value(
                estimated_annual_value=contract.estimated_annual_value,
                total_obligation=contract.total_obligation,
                award_amount=contract.award_amount,
                start_date=contract.start_date,
                expiration_date=contract.expiration_date,
            ),
            contract.expiration_date,
            recurring_fit_score=contract.recurring_fit_score or 0.4,
        )
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
