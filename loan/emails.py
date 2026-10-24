from django.conf import settings
from django.urls import reverse

from core.mailer import send_email
from wallet.models import Wallet

from .calculations import compute_state


def _notice(**kwargs):
    return send_email(from_email=settings.NOTICE_FROM_EMAIL, **kwargs)


def _admin_url(viewname, pk):
    base = getattr(settings, "BACKEND_URL", "").rstrip("/")
    return base + reverse(viewname, args=[pk])


def _pct(value) -> str:
    return format(value.normalize(), "f")


def _loan_context(loan, outstanding=None):
    """Template-friendly values. Templates use these instead of poking at the model.

        {{ principal }}           amount requested / borrowed
        {{ duration_label }}      "1 month"           {{ duration_days }}   30
        {{ daily_rate }}          0.2   (percent per day within the term)
        {{ overdue_rate }}        1     (percent per day after the due date)
        {{ due_at }} {{ halfway_at }}   (None until the loan is approved)
        {{ interest_accrued }} {{ total_due }} {{ amount_repaid }} {{ outstanding }}
        {{ loan_url }} {{ deposit_url }}
    """
    state = compute_state(loan)
    frontend = settings.FRONTEND_URL
    return {
        "loan": loan,
        "principal": loan.principal,
        "duration_label": loan.get_duration_display(),
        "duration_days": loan.duration_days,
        "daily_rate": _pct(loan.daily_rate_percent),
        "overdue_rate": _pct(loan.overdue_rate_percent),
        "due_at": loan.due_at,
        "halfway_at": loan.halfway_at,
        "interest_accrued": state["interest_accrued"],
        "total_due": state["total_due"],
        "amount_repaid": state["amount_repaid"],
        "outstanding": state["outstanding"] if outstanding is None else outstanding,
        "daily_interest_usd": state["daily_interest_usd"],
        "days_remaining": state["days_remaining"],
        "loan_url": frontend + getattr(settings, "FRONTEND_LOAN_PATH", "/loan"),
        "deposit_url": frontend + getattr(settings, "FRONTEND_DEPOSIT_PATH", "/deposit"),
    }


# ---- request lifecycle ------------------------------------------------------
def send_loan_requested_email(user, loan):
    return _notice(
        to=user.email,
        subject="We've received your SolvexMarketInc loan request",
        template="loan/loan_requested.html",
        context={"user": user, **_loan_context(loan)},
    )


def send_loan_approved_email(user, loan):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc loan has been approved",
        template="loan/loan_approved.html",
        context={"user": user, **_loan_context(loan)},
    )


def send_loan_rejected_email(user, loan):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc loan request was declined",
        template="loan/loan_rejected.html",
        context={"user": user, "reason": loan.decline_reason, **_loan_context(loan)},
    )


# ---- while active -----------------------------------------------------------
def send_loan_halfway_reminder_email(user, loan):
    return _notice(
        to=user.email,
        subject="Reminder: your SolvexMarketInc loan is halfway to its due date",
        template="loan/loan_halfway_reminder.html",
        context={"user": user, **_loan_context(loan)},
    )


def send_loan_overdue_notice_email(user, loan):
    return _notice(
        to=user.email,
        subject="Action required: your SolvexMarketInc loan is now overdue",
        template="loan/loan_overdue_notice.html",
        context={"user": user, **_loan_context(loan)},
    )


def send_loan_repayment_email(user, loan, repayment):
    return _notice(
        to=user.email,
        subject="We've received a payment on your SolvexMarketInc loan",
        template="loan/loan_repayment_received.html",
        context={
            "user": user, "repayment": repayment, "paid_amount": repayment.amount,
            "automatic": repayment.source == "deposit",
            **_loan_context(loan, outstanding=repayment.outstanding_after),
        },
    )


def send_loan_repaid_email(user, loan, repayment):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc loan is fully repaid",
        template="loan/loan_repaid.html",
        context={
            "user": user, "repayment": repayment, "paid_amount": repayment.amount,
            **_loan_context(loan, outstanding=repayment.outstanding_after),
        },
    )


# ---- staff alert ------------------------------------------------------------
def send_admin_new_loan_email(loan):
    recipient = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    if not recipient:
        return False
    balance = Wallet.objects.filter(user=loan.user).values_list("balance", flat=True).first()
    return _notice(
        to=recipient,
        subject=f"New loan request {loan.reference} (${loan.principal})",
        template="loan/admin_new_loan.html",
        context={
            "user_email": loan.user.email,
            "user_balance": balance,
            "admin_url": _admin_url("admin:loan_loan_change", loan.pk),
            **_loan_context(loan),
        },
    )