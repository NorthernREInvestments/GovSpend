from dataclasses import dataclass
from datetime import date, timedelta

OPTIONS_VALUE_THRESHOLD = 0.05
MIN_ANNUALIZATION_YEARS = 0.25
DAYS_PER_YEAR = 365.25
DEFAULT_MIN_ANNUAL = 50_000
DEFAULT_MAX_ANNUAL = 350_000

IDEAL_PERIOD_MIN = 0.85
IDEAL_PERIOD_MAX = 1.35
IDEAL_REMAINING_OPTIONS_MIN = 3.0
IDEAL_TOTAL_RUNWAY_MIN = 4.0

TOTAL_SMALL_BUSINESS_MARKERS = (
    "total small business",
    "total sb set-aside",
)

RECOMPETE_POP_FLAG = "Final Option Period — Recompete Likely"


@dataclass(frozen=True)
class PursuitEvaluation:
    pursuit_score: int
    priority_tier: str
    days_until_expiration: int
    bidders_display: str


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


def is_total_small_business_setaside(set_aside: str) -> bool:
    normalized = set_aside.strip().lower()
    return any(marker in normalized for marker in TOTAL_SMALL_BUSINESS_MARKERS)


def format_bidders_display(number_of_offers_received: int | None) -> str:
    if number_of_offers_received is None:
        return "Unknown bidders last time"
    count = int(number_of_offers_received)
    label = "bidder" if count == 1 else "bidders"
    return f"{count} {label} last time"


def _urgency_points(days_left: int) -> int:
    if days_left <= 90:
        return 35
    if days_left <= 180:
        return 20
    return 5


def _competition_points(number_of_offers_received: int | None) -> int:
    if number_of_offers_received is None:
        return 8
    offers = int(number_of_offers_received)
    if 1 <= offers <= 3:
        return 25
    if 4 <= offers <= 7:
        return 12
    return 0


def _annual_value_points(
    estimated_annual_value: float,
    *,
    min_annual_value: float,
    max_annual_value: float,
) -> int:
    if estimated_annual_value < min_annual_value or estimated_annual_value > max_annual_value:
        return 0
    span = max(max_annual_value - min_annual_value, 1)
    normalized = (estimated_annual_value - min_annual_value) / span
    return 15 + round(min(max(normalized, 0.0), 1.0) * 10)


def _set_aside_points(set_aside: str) -> int:
    return 10 if is_total_small_business_setaside(set_aside) else 0


def _recompete_points(pop_flag: str) -> int:
    if pop_flag == RECOMPETE_POP_FLAG:
        return 5
    if pop_flag == "Options Available":
        return 0
    return 2


def evaluate_pursuit(
    *,
    estimated_annual_value: float,
    expiration_date: date,
    number_of_offers_received: int | None = None,
    set_aside: str = "",
    pop_flag: str = "",
    min_annual_value: float = DEFAULT_MIN_ANNUAL,
    max_annual_value: float = DEFAULT_MAX_ANNUAL,
    today: date | None = None,
) -> PursuitEvaluation:
    today = today or date.today()
    days_left = (expiration_date - today).days
    in_range = annual_value_in_range(
        estimated_annual_value,
        min_annual_value,
        max_annual_value,
    )
    small_biz = is_total_small_business_setaside(set_aside)
    offers = number_of_offers_received

    score = (
        _urgency_points(days_left)
        + _competition_points(offers)
        + _annual_value_points(
            estimated_annual_value,
            min_annual_value=min_annual_value,
            max_annual_value=max_annual_value,
        )
        + _set_aside_points(set_aside)
        + _recompete_points(pop_flag)
    )
    score = max(1, min(100, score))

    force_low = (
        days_left > 180
        or not in_range
        or (offers is not None and offers >= 8)
    )
    if force_low:
        score = min(score, 40 if days_left > 180 or not in_range else 35)
        tier = "Low"
    elif (
        days_left <= 90
        and in_range
        and offers is not None
        and 1 <= offers <= 3
        and small_biz
    ):
        score = max(score, 85)
        tier = "High"
    elif (
        days_left <= 90
        and in_range
        and (offers is None or 1 <= offers <= 3)
        and score >= 75
    ):
        tier = "High"
    elif (
        days_left <= 180
        and in_range
        and offers is not None
        and 4 <= offers <= 7
    ):
        score = max(score, 55)
        tier = "Medium"
    elif days_left <= 180 and in_range and score >= 50:
        tier = "Medium"
    else:
        tier = "Low"

    return PursuitEvaluation(
        pursuit_score=score,
        priority_tier=tier,
        days_until_expiration=days_left,
        bidders_display=format_bidders_display(offers),
    )


def compute_pursuit_score(
    estimated_annual_value: float,
    expiration_date: date,
    *,
    number_of_offers_received: int | None = None,
    set_aside: str = "",
    pop_flag: str = "",
    min_annual_value: float = DEFAULT_MIN_ANNUAL,
    max_annual_value: float = DEFAULT_MAX_ANNUAL,
    today: date | None = None,
    **_legacy_kwargs,
) -> float:
    return float(
        evaluate_pursuit(
            estimated_annual_value=estimated_annual_value,
            expiration_date=expiration_date,
            number_of_offers_received=number_of_offers_received,
            set_aside=set_aside,
            pop_flag=pop_flag,
            min_annual_value=min_annual_value,
            max_annual_value=max_annual_value,
            today=today,
        ).pursuit_score
    )


def priority_tier(
    estimated_annual_value: float,
    expiration_date: date,
    *,
    number_of_offers_received: int | None = None,
    set_aside: str = "",
    pop_flag: str = "",
    min_annual_value: float = DEFAULT_MIN_ANNUAL,
    max_annual_value: float = DEFAULT_MAX_ANNUAL,
    today: date | None = None,
    **_legacy_kwargs,
) -> str:
    return evaluate_pursuit(
        estimated_annual_value=estimated_annual_value,
        expiration_date=expiration_date,
        number_of_offers_received=number_of_offers_received,
        set_aside=set_aside,
        pop_flag=pop_flag,
        min_annual_value=min_annual_value,
        max_annual_value=max_annual_value,
        today=today,
    ).priority_tier


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


def is_recompete_candidate(pop_flag: str) -> bool:
    """True when option ceiling is exhausted and a new competition is more likely."""
    return pop_flag == RECOMPETE_POP_FLAG


def apply_recompete_filter(contracts: list, *, recompete_only: bool) -> list:
    if not recompete_only:
        return contracts
    return [contract for contract in contracts if is_recompete_candidate(contract.pop_flag)]


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
    number_of_offers_received: int | None = None,
    set_aside: str = "",
    min_annual_value: float = DEFAULT_MIN_ANNUAL,
    max_annual_value: float = DEFAULT_MAX_ANNUAL,
    today: date | None = None,
    **_legacy_kwargs,
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
        number_of_offers_received=number_of_offers_received,
        set_aside=set_aside,
        pop_flag=pop_flag,
        min_annual_value=min_annual_value,
        max_annual_value=max_annual_value,
        today=today,
    )


def evaluate_contract_pursuit(
    contract,
    *,
    min_annual_value: float = DEFAULT_MIN_ANNUAL,
    max_annual_value: float = DEFAULT_MAX_ANNUAL,
    today: date | None = None,
) -> PursuitEvaluation:
    annual = effective_annual_value(
        estimated_annual_value=contract.estimated_annual_value,
        total_obligation=contract.total_obligation,
        award_amount=contract.award_amount,
        start_date=contract.start_date,
        expiration_date=contract.expiration_date,
    )
    return evaluate_pursuit(
        estimated_annual_value=annual,
        expiration_date=contract.expiration_date,
        number_of_offers_received=contract.number_of_offers_received,
        set_aside=contract.set_aside,
        pop_flag=contract.pop_flag,
        min_annual_value=min_annual_value,
        max_annual_value=max_annual_value,
        today=today,
    )
