from django.conf import settings
from django.urls import reverse

from core.mailer import send_email

from .models import Wallet


def _notice(**kwargs):
    return send_email(from_email=settings.NOTICE_FROM_EMAIL, **kwargs)


def _admin_url(viewname, pk):
    base = getattr(settings, "BACKEND_URL", "").rstrip("/")
    return base + reverse(viewname, args=[pk])


def _current_balance(user):
    return Wallet.objects.filter(user=user).values_list("balance", flat=True).first()


# ---- deposits ---------------------------------------------------------------
def send_deposit_requested_email(user, deposit):
    return _notice(
        to=user.email,
        subject="We've received your SolvexMarketInc deposit request",
        template="wallet/deposit_requested.html",
        context={"user": user, "deposit": deposit},
    )


def send_deposit_approved_email(user, deposit):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc deposit has been credited",
        template="wallet/deposit_approved.html",
        context={"user": user, "deposit": deposit, "new_balance": _current_balance(user)},
    )


def send_deposit_rejected_email(user, deposit):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc deposit request was declined",
        template="wallet/deposit_rejected.html",
        context={"user": user, "deposit": deposit, "reason": deposit.decline_reason},
    )


# ---- withdrawals ------------------------------------------------------------
def send_withdrawal_otp_email(user, withdrawal, otp_code):
    """Security email: uses DEFAULT_FROM_EMAIL (no from_email)."""
    return send_email(
        to=user.email,
        subject="Your SolvexMarketInc withdrawal confirmation code",
        template="wallet/withdrawal_otp.html",
        context={
            "user": user,
            "withdrawal": withdrawal,
            "otp_code": otp_code,
            "expiry_minutes": settings.PENDING_TRANSACTION_EXPIRY_MINUTES,
        },
    )


def send_withdrawal_requested_email(user, withdrawal):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc withdrawal request was received",
        template="wallet/withdrawal_requested.html",
        context={"user": user, "withdrawal": withdrawal},
    )


def send_withdrawal_completed_email(user, withdrawal):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc withdrawal has been sent",
        template="wallet/withdrawal_completed.html",
        context={"user": user, "withdrawal": withdrawal},
    )


def send_withdrawal_failed_email(user, withdrawal):
    return _notice(
        to=user.email,
        subject="Your SolvexMarketInc withdrawal could not be processed",
        template="wallet/withdrawal_failed.html",
        context={"user": user, "withdrawal": withdrawal, "reason": withdrawal.failure_reason},
    )


# ---- staff alerts -----------------------------------------------------------
def send_admin_new_deposit_email(deposit):
    recipient = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    if not recipient:
        return False
    return _notice(
        to=recipient,
        subject=f"New deposit request {deposit.reference}",
        template="wallet/admin_new_deposit.html",
        context={
            "deposit": deposit,
            "user_email": deposit.user.email,
            "admin_url": _admin_url("admin:wallet_deposit_change", deposit.pk),
        },
    )


def send_admin_new_withdrawal_email(withdrawal):
    recipient = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    if not recipient:
        return False
    return _notice(
        to=recipient,
        subject=f"New withdrawal to process {withdrawal.reference}",
        template="wallet/admin_new_withdrawal.html",
        context={
            "withdrawal": withdrawal,
            "user_email": withdrawal.user.email,
            "admin_url": _admin_url("admin:wallet_withdrawal_change", withdrawal.pk),
        },
    )
