from datetime import date, timedelta

OPTIONS_VALUE_THRESHOLD = 0.05
MIN_CONTRACT_YEARS = 1.0
DAYS_PER_YEAR = 365.25


def contract_length_years(start_date: date, end_date: date) -> float:
    days = (end_date - start_date).days
    if days <= 0:
        return MIN_CONTRACT_YEARS
    return max(days / DAYS_PER_YEAR, MIN_CONTRACT_YEARS)


def compute_estimated_annual_value(
    total_obligation: float,
    start_date: date | None,
    end_date: date,
) -> float | None:
    if start_date is None or total_obligation <= 0:
        return None
    years = contract_length_years(start_date, end_date)
    return round(total_obligation / years, 2)


def compute_pop_flag(
    base_exercised_options_value: float | None,
    base_all_options_value: float | None,
) -> str:
    exercised = base_exercised_options_value or 0
    all_options = base_all_options_value or 0
    if exercised <= 0 or all_options <= 0:
        return ""

    if exercised >= all_options * (1 - OPTIONS_VALUE_THRESHOLD):
        return "Final Option Period — Recompete Likely"
    if all_options > exercised * (1 + OPTIONS_VALUE_THRESHOLD):
        return "Options Available"
    return "Final Option Period — Recompete Likely"


def compute_pursuit_score(
    estimated_annual_value: float,
    expiration_date: date,
    *,
    max_annual_value: float = 350_000,
    today: date | None = None,
) -> float:
    """Higher score = act sooner. Balances urgency (days left) and est. annual value."""
    today = today or date.today()
    days_left = max((expiration_date - today).days, 1)
    urgency = (61 - min(days_left, 60)) / 60
    value = min(estimated_annual_value / max(max_annual_value, 1), 1.0)
    return round(urgency * 0.55 + value * 0.45, 4)


def priority_tier(
    estimated_annual_value: float,
    expiration_date: date,
    today: date | None = None,
) -> str:
    today = today or date.today()
    days_left = (expiration_date - today).days
    if days_left <= 21 and estimated_annual_value >= 125_000:
        return "High"
    if days_left <= 14 and estimated_annual_value >= 75_000:
        return "High"
    if days_left <= 45 or estimated_annual_value >= 175_000:
        return "Medium"
    return "Low"


def usaspending_award_url(generated_internal_id: str | None) -> str:
    if not generated_internal_id:
        return ""
    return f"https://www.usaspending.gov/award/{generated_internal_id}"


def watchlist_priority(
    estimated_annual_value: float,
    expiration_date: date,
    today: date | None = None,
) -> str:
    """High/Medium/Low priority for the watchlist table."""
    today = today or date.today()
    days_left = (expiration_date - today).days
    if days_left <= 60 and estimated_annual_value >= 100_000:
        return "High"
    if days_left <= 120 and estimated_annual_value >= 50_000:
        return "Medium"
    return "Low"


def expected_repost_dates(expiration_date: date) -> tuple[date, date]:
    return expiration_date - timedelta(days=120), expiration_date - timedelta(days=30)


def annual_value_in_range(
    estimated_annual_value: float | None,
    min_annual_value: float,
    max_annual_value: float | None,
) -> bool:
    if estimated_annual_value is None:
        return False
    if estimated_annual_value < min_annual_value:
        return False
    if max_annual_value is not None and estimated_annual_value > max_annual_value:
        return False
    return True


def contract_total_obligation(
    *,
    total_obligation: float = 0,
    award_amount: float = 0,
) -> float:
    return total_obligation if total_obligation > 0 else award_amount


def effective_annual_value(
    *,
    estimated_annual_value: float = 0,
    total_obligation: float = 0,
    award_amount: float = 0,
    start_date: date | None,
    expiration_date: date,
) -> float:
    if estimated_annual_value > 0:
        return estimated_annual_value
    obligation = contract_total_obligation(
        total_obligation=total_obligation,
        award_amount=award_amount,
    )
    if start_date is None or obligation <= 0:
        return 0.0
    return compute_estimated_annual_value(obligation, start_date, expiration_date) or 0.0


def pursuit_score_for_contract(
    *,
    estimated_annual_value: float,
    total_obligation: float,
    award_amount: float,
    start_date: date | None,
    expiration_date: date,
    max_annual_value: float = 350_000,
    today: date | None = None,
) -> float:
    annual = effective_annual_value(
        estimated_annual_value=estimated_annual_value,
        total_obligation=total_obligation,
        award_amount=award_amount,
        start_date=start_date,
        expiration_date=expiration_date,
    )
    if annual <= 0:
        return 0.0
    return compute_pursuit_score(
        annual,
        expiration_date,
        max_annual_value=max_annual_value,
        today=today,
    )
