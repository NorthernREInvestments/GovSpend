from datetime import date, timedelta

OPTIONS_VALUE_THRESHOLD = 0.05
MIN_ANNUALIZATION_YEARS = 0.25
DAYS_PER_YEAR = 365.25

IDEAL_PERIOD_MIN = 0.85
IDEAL_PERIOD_MAX = 1.35
IDEAL_REMAINING_OPTIONS_MIN = 3.0
IDEAL_TOTAL_RUNWAY_MIN = 4.0


def actual_period_years(start_date: date, end_date: date) -> float | None:
    days = (end_date - start_date).days
    if days <= 0:
        return None
    return round(days / DAYS_PER_YEAR, 2)


def annualization_years(start_date: date, end_date: date) -> float | None:
    period = actual_period_years(start_date, end_date)
    if period is None:
        return None
    return max(period, MIN_ANNUALIZATION_YEARS)


def contract_length_years(start_date: date, end_date: date) -> float:
    years = annualization_years(start_date, end_date)
    if years is None:
        return MIN_ANNUALIZATION_YEARS
    return years


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


def compute_recurring_profile(
    *,
    start_date: date | None,
    expiration_date: date,
    potential_end_date: date | None = None,
    pop_flag: str = "",
) -> dict[str, float | str | None]:
    period_years = (
        actual_period_years(start_date, expiration_date) if start_date else None
    )
    remaining_option_years = None
    total_runway_years = None

    if start_date:
        runway_end = potential_end_date or expiration_date
        if runway_end > start_date:
            total_runway_years = actual_period_years(start_date, runway_end)
        if (
            potential_end_date
            and potential_end_date > expiration_date
        ):
            remaining_option_years = actual_period_years(
                expiration_date,
                potential_end_date,
            )

    is_annual_period = (
        period_years is not None
        and IDEAL_PERIOD_MIN <= period_years <= IDEAL_PERIOD_MAX
    )
    options_available = pop_flag == "Options Available"
    recompete_likely = "Recompete" in pop_flag
    long_runway = (
        (remaining_option_years or 0) >= IDEAL_REMAINING_OPTIONS_MIN
        or (total_runway_years or 0) >= IDEAL_TOTAL_RUNWAY_MIN
    )

    if is_annual_period and long_runway and options_available:
        recurring_fit = "Ideal Recurring — 1yr + options"
        recurring_fit_score = 1.0
    elif is_annual_period and long_runway:
        recurring_fit = "Strong Runway — multi-year options"
        recurring_fit_score = 0.85
    elif is_annual_period and recompete_likely:
        recurring_fit = "Annual recompete"
        recurring_fit_score = 0.65
    elif is_annual_period:
        recurring_fit = "Annual period"
        recurring_fit_score = 0.55
    elif period_years is not None and period_years < 0.5:
        recurring_fit = "Short period"
        recurring_fit_score = 0.25
    else:
        recurring_fit = "Standard"
        recurring_fit_score = 0.4

    return {
        "period_years": period_years,
        "remaining_option_years": remaining_option_years,
        "total_runway_years": total_runway_years,
        "recurring_fit": recurring_fit,
        "recurring_fit_score": recurring_fit_score,
    }


def format_period_years(years: float | None) -> str:
    if years is None:
        return "—"
    if years < 1.05:
        months = max(1, round(years * 12))
        return f"{months} mo"
    return f"{years:.1f} yrs"


def compute_pursuit_score(
    estimated_annual_value: float,
    expiration_date: date,
    *,
    max_annual_value: float = 350_000,
    recurring_fit_score: float = 0.4,
    today: date | None = None,
) -> float:
    """Higher score = act sooner. Balances urgency, est. annual value, and recurring fit."""
    today = today or date.today()
    days_left = max((expiration_date - today).days, 1)
    urgency = (61 - min(days_left, 60)) / 60
    value = min(estimated_annual_value / max(max_annual_value, 1), 1.0)
    recurring = min(max(recurring_fit_score, 0.0), 1.0)
    base = urgency * 0.50 + value * 0.35 + recurring * 0.15
    return round(min(base, 1.0), 4)


def priority_tier(
    estimated_annual_value: float,
    expiration_date: date,
    *,
    recurring_fit_score: float = 0.4,
    today: date | None = None,
) -> str:
    today = today or date.today()
    days_left = (expiration_date - today).days
    if recurring_fit_score >= 0.85 and days_left <= 60 and estimated_annual_value >= 50_000:
        return "High"
    if days_left <= 21 and estimated_annual_value >= 125_000:
        return "High"
    if days_left <= 14 and estimated_annual_value >= 75_000:
        return "High"
    if days_left <= 45 or estimated_annual_value >= 175_000:
        return "Medium"
    if recurring_fit_score >= 1.0 and estimated_annual_value >= 50_000:
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
    potential_end_date: date | None = None,
    pop_flag: str = "",
    recurring_fit_score: float | None = None,
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
    if recurring_fit_score is None:
        recurring_fit_score = compute_recurring_profile(
            start_date=start_date,
            expiration_date=expiration_date,
            potential_end_date=potential_end_date,
            pop_flag=pop_flag,
        )["recurring_fit_score"]
    return compute_pursuit_score(
        annual,
        expiration_date,
        max_annual_value=max_annual_value,
        recurring_fit_score=float(recurring_fit_score or 0.4),
        today=today,
    )
