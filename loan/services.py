"""
loan/services.py - ALL loan business logic.

Public contract used by other apps / wallet:

    apply_deposit_to_loan(deposit, wallet)  called by wallet.services.complete_deposit
    outstanding_for_user(user) -> Decimal
    account_snapshot(user, wallet=None) -> dict   (balance, loan_outstanding, net_balance, has_active_loan)
    assert_can_withdraw(user)               called by wallet.services.create_withdrawal
    approve_loan / reject_loan              admin only (Django admin)

Flow
----
user: request_loan()  -> status pending (no money, no interest, nothing owed yet)
admin: approve_loan() -> status active, the clock starts, "approved" email is sent
       reject_loan()  -> status rejected (reason required), "declined" email is sent
user: cancel_loan()   -> pending -> cancelled

Money model
-----------
* wallet.balance is always a REAL, non-negative number (the wallet DB constraints stay intact).
* The debt lives on the Loan row (ACTIVE loans only). The "account balance" shown to users is
      net_balance = wallet.balance - loan_outstanding        (can be negative)
* settings.LOAN_CREDIT_TO_WALLET (default False):
      False -> on approval the principal is recorded as debt only; wallet.balance is untouched.
      True  -> on approval the principal is also credited to wallet.balance (spendable).
* Repayments (automatic from deposits, or manual) DEBIT wallet.balance with a `loan_repayment` ledger entry.

Locking order everywhere: wallet row first, then the loan row.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import BusinessError
from core.utils import money
from wallet import services as wallet_services
from wallet.models import EntryType

from . import emails
from .calculations import compute_state
from .models import (
    DURATION_DAYS, OPEN_STATUSES, Loan, LoanDuration, LoanRepayment, LoanStatus, RepaymentSource,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
CRON_BATCH_SIZE = 100


# --------------------------------------------------------------------------- #
# Settings helpers (all optional, with defaults)
# --------------------------------------------------------------------------- #
def _dec(name, default) -> Decimal:
    return Decimal(str(getattr(settings, name, default)))


def min_amount() -> Decimal:
    return money(_dec("LOAN_MIN_AMOUNT", "10.00"))


def max_amount() -> Decimal:
    return money(_dec("LOAN_MAX_AMOUNT", "10000.00"))


def max_eligible_balance() -> Decimal:
    return money(_dec("LOAN_MAX_ELIGIBLE_BALANCE", "50.00"))


def daily_rate_percent() -> Decimal:
    return _dec("LOAN_DAILY_INTEREST_PERCENT", "0.20").quantize(Decimal("0.01"))


def overdue_rate_percent() -> Decimal:
    return _dec("LOAN_OVERDUE_INTEREST_PERCENT", "1.00").quantize(Decimal("0.01"))


def credit_to_wallet() -> bool:
    return bool(getattr(settings, "LOAN_CREDIT_TO_WALLET", False))


def block_withdrawals() -> bool:
    return bool(getattr(settings, "LOAN_BLOCK_WITHDRAWALS_WHILE_ACTIVE", True))


def _fmt(value) -> str:
    return format(money(value), "f")


def _pct(value) -> str:
    return format(Decimal(value).normalize(), "f")


def _not_found():
    return BusinessError("not_found", "Not found.", status=404)


def _open_loan_error(loan=None):
    """409 for 'you already have a pending request / active loan'."""
    if loan is not None and loan.status == LoanStatus.PENDING:
        return BusinessError(
            "loan_request_pending",
            "You already have a loan request waiting for approval.",
            status=409, data={"loan_id": str(loan.pk), "status": loan.status},
        )
    return BusinessError(
        "active_loan_exists",
        "You already have an active loan. Repay it in full before borrowing again.",
        status=409, data={"loan_id": str(loan.pk) if loan else None, "status": LoanStatus.ACTIVE},
    )


# --------------------------------------------------------------------------- #
# Read helpers
# --------------------------------------------------------------------------- #
def get_active_loan(user):
    return Loan.objects.filter(user=user, status=LoanStatus.ACTIVE).first()


def get_open_loan(user):
    """The user's pending-or-active loan (there can only be one)."""
    return Loan.objects.filter(user=user, status__in=OPEN_STATUSES).first()


def outstanding_for_user(user) -> Decimal:
    """Debt only exists for ACTIVE loans; a pending request owes nothing."""
    loan = get_active_loan(user)
    return compute_state(loan)["outstanding"] if loan else ZERO


def account_snapshot(user, wallet=None) -> dict:
    """What the frontend should show as the account balance: `net_balance` (may be negative)."""
    wallet = wallet or wallet_services.get_or_create_wallet(user)
    outstanding = outstanding_for_user(user)
    return {
        "balance": _fmt(wallet.balance),
        "loan_outstanding": _fmt(outstanding),
        "net_balance": _fmt(wallet.balance - outstanding),
        "has_active_loan": outstanding > 0,
    }


def get_config() -> dict:
    return {
        "currency": "USD",
        "min_loan_usd": _fmt(min_amount()),
        "max_loan_usd": _fmt(max_amount()),
        "max_eligible_balance_usd": _fmt(max_eligible_balance()),
        "daily_interest_percent": _pct(daily_rate_percent()),
        "overdue_daily_interest_percent": _pct(overdue_rate_percent()),
        "durations": [
            {"value": value, "label": str(label), "days": DURATION_DAYS[LoanDuration(value)]}
            for value, label in LoanDuration.choices
        ],
        "requires_admin_approval": True,
        "principal_credited_to_wallet": credit_to_wallet(),
    }


def get_eligibility(user) -> dict:
    wallet = wallet_services.get_or_create_wallet(user)
    open_loan = get_open_loan(user)
    reasons = []
    if open_loan is not None:
        reasons.append(
            "loan_request_pending" if open_loan.status == LoanStatus.PENDING else "active_loan_exists"
        )
    if wallet.balance >= max_eligible_balance():
        reasons.append("balance_too_high")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "balance": _fmt(wallet.balance),
        "max_eligible_balance_usd": _fmt(max_eligible_balance()),
        "min_loan_usd": _fmt(min_amount()),
        "max_loan_usd": _fmt(max_amount()),
        "open_loan_id": str(open_loan.pk) if open_loan else None,
        "open_loan_status": open_loan.status if open_loan else None,
    }


# --------------------------------------------------------------------------- #
# Request / cancel (user side)
# --------------------------------------------------------------------------- #
def request_loan(user, amount, duration) -> Loan:
    """Creates a PENDING request. Nothing is owed and no interest runs until an admin approves."""
    duration = LoanDuration(duration)
    amount = money(amount)
    if amount < min_amount():
        raise BusinessError(
            "loan_below_minimum", f"The minimum loan is {_fmt(min_amount())} USD.",
            data={"min_loan_usd": _fmt(min_amount())},
        )
    if amount > max_amount():
        raise BusinessError(
            "loan_above_maximum", f"The maximum loan is {_fmt(max_amount())} USD.",
            data={"max_loan_usd": _fmt(max_amount())},
        )

    try:
        with transaction.atomic():
            wallet = wallet_services._locked_wallet(user)  # per-user mutex (wallet row first)
            existing = get_open_loan(user)
            if existing is not None:
                raise _open_loan_error(existing)
            if wallet.balance >= max_eligible_balance():
                raise BusinessError(
                    "balance_too_high",
                    f"You can only borrow when your balance is below {_fmt(max_eligible_balance())} USD.",
                    data={
                        "balance": _fmt(wallet.balance),
                        "max_eligible_balance_usd": _fmt(max_eligible_balance()),
                    },
                )
            loan = Loan.objects.create(
                user=user,
                reference=wallet_services._new_reference(Loan, "LN-"),
                principal=amount,
                duration=duration.value,
                duration_days=DURATION_DAYS[duration],
                daily_rate_percent=daily_rate_percent(),
                overdue_rate_percent=overdue_rate_percent(),
                status=LoanStatus.PENDING,
            )
            transaction.on_commit(lambda: emails.send_loan_requested_email(user, loan))
            transaction.on_commit(lambda: emails.send_admin_new_loan_email(loan))
    except IntegrityError:
        raise _open_loan_error()
    return loan


def cancel_loan(user, loan_id) -> Loan:
    with transaction.atomic():
        loan = Loan.objects.select_for_update().filter(pk=loan_id, user=user).first()
        if loan is None:
            raise _not_found()
        if loan.status != LoanStatus.PENDING:
            raise BusinessError(
                "conflict", "Only loan requests waiting for approval can be cancelled.",
                status=409, data={"status": loan.status},
            )
        loan.status = LoanStatus.CANCELLED
        loan.save(update_fields=["status", "updated_at"])
    return loan


# --------------------------------------------------------------------------- #
# Approve / reject (admin side)
# --------------------------------------------------------------------------- #
def approve_loan(loan, admin_user) -> None:
    """pending -> active. Starts the clock, optionally credits the principal, emails the user."""
    with transaction.atomic():
        wallet = wallet_services._locked_wallet(loan.user)
        locked = Loan.objects.select_for_update().get(pk=loan.pk)
        if locked.status != LoanStatus.PENDING:
            return

        now = timezone.now()
        due_at = now + timedelta(days=locked.duration_days)
        locked.status = LoanStatus.ACTIVE
        locked.started_at = now
        locked.due_at = due_at
        locked.halfway_at = now + (due_at - now) / 2
        locked.credited_to_wallet = credit_to_wallet()
        locked.decline_reason = ""
        locked.processed_by = admin_user
        locked.processed_at = now
        locked.save(update_fields=[
            "status", "started_at", "due_at", "halfway_at", "credited_to_wallet",
            "decline_reason", "processed_by", "processed_at", "updated_at",
        ])
        if locked.credited_to_wallet:
            wallet_services._apply(
                wallet, EntryType.LOAN_DISBURSEMENT, locked.principal, balance=locked.principal,
                reference=f"loan:{locked.pk}",
                description=f"Loan {locked.reference} approved",
            )
        transaction.on_commit(lambda: emails.send_loan_approved_email(locked.user, locked))
    loan.refresh_from_db()


def reject_loan(loan, admin_user, reason) -> None:
    """pending -> rejected. A reason is required so the user knows why."""
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("decline_reason_required", "A reason is required to reject a loan request.")
    with transaction.atomic():
        locked = Loan.objects.select_for_update().get(pk=loan.pk)
        if locked.status != LoanStatus.PENDING:
            return
        locked.status = LoanStatus.REJECTED
        locked.decline_reason = reason
        locked.processed_by = admin_user
        locked.processed_at = timezone.now()
        locked.save(update_fields=["status", "decline_reason", "processed_by", "processed_at", "updated_at"])
        transaction.on_commit(lambda: emails.send_loan_rejected_email(locked.user, locked))
    loan.refresh_from_db()


# --------------------------------------------------------------------------- #
# Repay
# --------------------------------------------------------------------------- #
def _settle(wallet, loan, available, source, reference, now):
    """Pay min(available, outstanding) from a LOCKED wallet onto a LOCKED active loan.
    Returns the LoanRepayment, or None if nothing was paid."""
    state = compute_state(loan, now)
    outstanding = state["outstanding"]
    if outstanding <= 0:  # defensive: nothing left to pay, just close it
        loan.status = LoanStatus.REPAID
        loan.closed_at = loan.closed_at or now
        loan.save(update_fields=["status", "closed_at", "updated_at"])
        return None

    pay = min(money(available), outstanding)
    if pay <= 0:
        return None
    remaining = money(outstanding - pay)

    repayment = LoanRepayment.objects.create(
        loan=loan, user_id=loan.user_id, amount=pay, source=source,
        reference=reference, outstanding_after=remaining,
    )
    wallet_services._apply(
        wallet, EntryType.LOAN_REPAYMENT, pay, balance=-pay,
        reference=f"loan_repayment:{repayment.pk}",
        description=f"Loan {loan.reference} repayment ({_fmt(pay)} USD)",
    )

    loan.amount_repaid = money(loan.amount_repaid + pay)
    fields = ["amount_repaid", "updated_at"]
    fully_repaid = remaining <= 0
    if fully_repaid:
        loan.status = LoanStatus.REPAID
        loan.closed_at = now
        fields += ["status", "closed_at"]
    loan.save(update_fields=fields)

    user = loan.user
    if fully_repaid:
        transaction.on_commit(lambda: emails.send_loan_repaid_email(user, loan, repayment))
    else:
        transaction.on_commit(lambda: emails.send_loan_repayment_email(user, loan, repayment))
    return repayment


def apply_deposit_to_loan(deposit, wallet):
    """Called from wallet.services.complete_deposit, INSIDE its transaction, right after the deposit
    was credited. Uses up to `deposit.usd_amount` to pay the ACTIVE loan. Idempotent.
    A loan that is still pending is not affected."""
    reference = f"deposit:{deposit.pk}"
    if LoanRepayment.objects.filter(reference=reference).exists():
        return None
    loan = Loan.objects.select_for_update().filter(user_id=deposit.user_id, status=LoanStatus.ACTIVE).first()
    if loan is None:
        return None
    return _settle(wallet, loan, deposit.usd_amount, RepaymentSource.DEPOSIT, reference, timezone.now())


def repay_from_wallet(user, loan_id, amount=None):
    """Manual repayment from real wallet funds. Returns (loan, repayment, wallet)."""
    with transaction.atomic():
        wallet = wallet_services._locked_wallet(user)
        loan = Loan.objects.select_for_update().filter(pk=loan_id, user=user).first()
        if loan is None:
            raise _not_found()
        if loan.status == LoanStatus.REPAID:
            raise BusinessError(
                "conflict", "This loan has already been repaid.", status=409, data={"status": loan.status}
            )
        if loan.status != LoanStatus.ACTIVE:
            raise BusinessError(
                "loan_not_active", "This loan is not active, so there is nothing to repay.",
                status=409, data={"status": loan.status},
            )

        now = timezone.now()
        outstanding = compute_state(loan, now)["outstanding"]
        available = money(wallet.available_balance)
        info = {
            **wallet_services.balance_strings(wallet),
            "loan_outstanding": _fmt(outstanding),
            "action": "deposit",
        }
        if available <= 0:
            raise BusinessError(
                "no_funds_to_repay",
                "There is no money in your account balance. Deposit funds to repay your loan.",
                data=info,
            )
        if amount is not None:
            amount = money(amount)
            if amount > available:
                raise BusinessError(
                    "insufficient_balance",
                    "The amount is more than your available balance. Deposit funds to repay more.",
                    data=info,
                )
            cap = amount
        else:
            cap = available

        repayment = _settle(wallet, loan, cap, RepaymentSource.WALLET, "", now)
    return loan, repayment, wallet


# --------------------------------------------------------------------------- #
# Wallet integration helpers
# --------------------------------------------------------------------------- #
def assert_can_withdraw(user):
    """Called by wallet.services.create_withdrawal. Only an ACTIVE loan blocks withdrawals."""
    if not block_withdrawals():
        return
    loan = get_active_loan(user)
    if loan is not None:
        raise BusinessError(
            "active_loan",
            "Repay your active loan before withdrawing.",
            status=409,
            data={"loan_id": str(loan.pk), "loan_outstanding": _fmt(compute_state(loan)["outstanding"])},
        )


# --------------------------------------------------------------------------- #
# Cron: reminders (ACTIVE loans only)
# --------------------------------------------------------------------------- #
def _claim(pk, field, now) -> bool:
    """Atomically mark a notice as sent so overlapping cron runs never double-send."""
    return Loan.objects.filter(
        pk=pk, status=LoanStatus.ACTIVE, **{f"{field}__isnull": True}
    ).update(**{field: now}) == 1


def _release(pk, field):
    Loan.objects.filter(pk=pk).update(**{field: None})


def _notify(loan, field, sender, now) -> bool:
    if not _claim(loan.pk, field, now):
        return False
    try:
        ok = bool(sender(loan.user, loan))
    except Exception:
        logger.exception("Loan notice %s failed for %s", field, loan.reference)
        ok = False
    if not ok:
        _release(loan.pk, field)  # retry on the next run
    return ok


def process_reminders() -> dict:
    """Halfway reminders + overdue notices. Idempotent; safe to run every few minutes."""
    now = timezone.now()
    halfway = overdue = 0

    halfway_qs = (
        Loan.objects.filter(
            status=LoanStatus.ACTIVE, halfway_reminder_sent_at__isnull=True,
            halfway_at__lte=now, due_at__gt=now,
        ).select_related("user").order_by("halfway_at")[:CRON_BATCH_SIZE]
    )
    for loan in halfway_qs:
        if _notify(loan, "halfway_reminder_sent_at", emails.send_loan_halfway_reminder_email, now):
            halfway += 1

    overdue_qs = (
        Loan.objects.filter(
            status=LoanStatus.ACTIVE, overdue_notice_sent_at__isnull=True, due_at__lte=now,
        ).select_related("user").order_by("due_at")[:CRON_BATCH_SIZE]
    )
    for loan in overdue_qs:
        if _notify(loan, "overdue_notice_sent_at", emails.send_loan_overdue_notice_email, now):
            overdue += 1

    return {"halfway_reminders_sent": halfway, "overdue_notices_sent": overdue}