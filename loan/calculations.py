"""
loan/calculations.py - the ONE place interest maths lives (pure functions, no DB writes).

Rules
-----
* Only ACTIVE and REPAID loans carry interest. Pending / rejected / cancelled loans are all zeros.
* Interest is simple interest on the ORIGINAL principal (partial repayments do not reduce it).
* The clock starts at `started_at` (= the moment the admin approved). A day counts per COMPLETED 24h:
      elapsed_days     = floor((as_of - started_at) / 24h)
      within_term_days = min(elapsed_days, duration_days)    -> daily_rate_percent   (0.20%)
      overdue_days     = max(elapsed_days - duration_days, 0) -> overdue_rate_percent (1.00%)
* total_due   = principal + interest_accrued
* outstanding = total_due - amount_repaid           (never below 0)
* For a repaid loan, `as_of` is frozen at `closed_at`, so interest stops growing.
"""
import math
from decimal import Decimal

from django.utils import timezone

from core.utils import money

from .models import LoanStatus

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
SECONDS_PER_DAY = 86400


def _empty_state(loan) -> dict:
    return {
        "days_elapsed": 0,
        "days_remaining": 0,
        "overdue_days": 0,
        "is_overdue": False,
        "current_rate_percent": Decimal(loan.daily_rate_percent),
        "daily_interest_usd": ZERO,
        "interest_accrued": ZERO,
        "total_due": ZERO,
        "amount_repaid": money(loan.amount_repaid),
        "outstanding": ZERO,
    }


def compute_state(loan, now=None) -> dict:
    if loan.status not in (LoanStatus.ACTIVE, LoanStatus.REPAID) or loan.started_at is None:
        return _empty_state(loan)

    now = now or timezone.now()
    active = loan.status == LoanStatus.ACTIVE
    as_of = now if active else (loan.closed_at or now)
    if as_of < loan.started_at:
        as_of = loan.started_at

    elapsed = int((as_of - loan.started_at).total_seconds() // SECONDS_PER_DAY)
    within_term = min(elapsed, loan.duration_days)
    overdue_days = max(elapsed - loan.duration_days, 0)

    principal = money(loan.principal)
    interest = money(
        principal * Decimal(loan.daily_rate_percent) / HUNDRED * within_term
        + principal * Decimal(loan.overdue_rate_percent) / HUNDRED * overdue_days
    )
    total_due = money(principal + interest)
    repaid = money(loan.amount_repaid)
    outstanding = max(money(total_due - repaid), ZERO) if active else ZERO

    is_overdue = active and now > loan.due_at
    if active and not is_overdue:
        days_remaining = max(int(math.ceil((loan.due_at - now).total_seconds() / SECONDS_PER_DAY)), 0)
    else:
        days_remaining = 0
    current_rate = Decimal(loan.overdue_rate_percent if is_overdue else loan.daily_rate_percent)

    return {
        "days_elapsed": elapsed,
        "days_remaining": days_remaining,
        "overdue_days": overdue_days,
        "is_overdue": is_overdue,
        "current_rate_percent": current_rate,
        "daily_interest_usd": money(principal * current_rate / HUNDRED) if active else ZERO,
        "interest_accrued": interest,
        "total_due": total_due,
        "amount_repaid": repaid,
        "outstanding": outstanding,
    }