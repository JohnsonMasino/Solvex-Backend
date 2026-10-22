from django.conf import settings
from django.urls import reverse

from core.mailer import send_email


def _attempts_remaining(user) -> int:
    used = user.kyc_submissions.count()
    return max(0, settings.KYC_MAX_ATTEMPTS - used)


def send_kyc_submitted_email(user, submission):
    return send_email(
        to=user.email,
        subject="We've received your SolvexMarketInc identity verification",
        template="kyc/kyc_submitted.html",
        context={"user": user, "submission": submission},
        from_email=settings.NOTICE_FROM_EMAIL,
    )


def send_kyc_approved_email(user, submission):
    return send_email(
        to=user.email,
        subject="Your SolvexMarketInc identity verification is approved",
        template="kyc/kyc_approved.html",
        context={"user": user, "submission": submission},
        from_email=settings.NOTICE_FROM_EMAIL,
    )


def send_kyc_rejected_email(user, submission):
    return send_email(
        to=user.email,
        subject="Action needed: your SolvexMarketInc identity verification",
        template="kyc/kyc_rejected.html",
        context={
            "user": user,
            "submission": submission,
            "reason": submission.rejection_reason,
            "attempts_remaining": _attempts_remaining(user),
        },
        from_email=settings.NOTICE_FROM_EMAIL,
    )


def send_admin_new_kyc_email(submission):
    """Staff alert. Deliberately contains no ID number or other sensitive identity data."""
    recipient = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    if not recipient:
        return False
    base = getattr(settings, "BACKEND_URL", "").rstrip("/")
    admin_url = base + reverse("admin:kyc_kycsubmission_change", args=[submission.pk])
    return send_email(
        to=recipient,
        subject="New KYC submission awaiting review",
        template="kyc/admin_new_kyc.html",
        context={
            "submission": submission,
            "user_email": submission.user.email,
            "admin_url": admin_url,
        },
        from_email=settings.NOTICE_FROM_EMAIL,
    )
