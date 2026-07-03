from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import Contract, ContractStatus
from app.schemas import DashboardStats
from app.services.scoring import priority_tier


def pursuit_contracts_query(db: Session):
    return db.query(Contract).order_by(
        Contract.pursuit_score.desc(),
        Contract.expiration_date.asc(),
        Contract.award_amount.desc(),
    )


def build_dashboard_data(db: Session) -> dict:
    all_contracts = pursuit_contracts_query(db).all()
    actionable = [
        contract
        for contract in all_contracts
        if contract.status not in (ContractStatus.WON, ContractStatus.LOST)
    ]
    contract_rows = actionable or all_contracts
    hot_leads = [
        contract
        for contract in contract_rows
        if priority_tier(contract.estimated_annual_value, contract.expiration_date) == "High"
    ][:5]

    today = date.today()
    if today.month == 12:
        month_end = date(today.year, 12, 31)
    else:
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1)

    total_value = sum(contract.estimated_annual_value for contract in contract_rows)
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
