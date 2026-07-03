from datetime import date, timedelta


def compute_pursuit_score(award_amount: float, expiration_date: date, today: date | None = None) -> float:
    """Higher score = act sooner. Balances urgency (days left) and deal size."""
    today = today or date.today()
    days_left = max((expiration_date - today).days, 1)
    urgency = (61 - min(days_left, 60)) / 60
    value = min(award_amount / 2_000_000, 1.0)
    return round(urgency * 0.55 + value * 0.45, 4)


def priority_tier(award_amount: float, expiration_date: date, today: date | None = None) -> str:
    today = today or date.today()
    days_left = (expiration_date - today).days
    if days_left <= 21 and award_amount >= 250_000:
        return "High"
    if days_left <= 14 and award_amount >= 100_000:
        return "High"
    if days_left <= 45 or award_amount >= 500_000:
        return "Medium"
    return "Low"


def usaspending_award_url(generated_internal_id: str | None) -> str:
    if not generated_internal_id:
        return ""
    return f"https://www.usaspending.gov/award/{generated_internal_id}"


def watchlist_priority(award_amount: float, expiration_date: date, today: date | None = None) -> str:
    """High/Medium/Low priority for the watchlist table."""
    today = today or date.today()
    days_left = (expiration_date - today).days
    if days_left <= 60 and award_amount > 100_000:
        return "High"
    if days_left <= 120 and award_amount > 50_000:
        return "Medium"
    return "Low"


def expected_repost_dates(expiration_date: date) -> tuple[date, date]:
    return expiration_date - timedelta(days=120), expiration_date - timedelta(days=30)
