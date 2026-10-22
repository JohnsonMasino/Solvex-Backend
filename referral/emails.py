"""One function per outbound referral email. All sent from NOTICE_FROM_EMAIL."""
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from core.mailer import send_email
from core.utils import money
from referral.models import Referral
from referral.utils import mask_name


def _first_name(user) -> str:
    parts = (user.full_name or "").split()
    return parts[0] if parts else "there"


def _dashboard_url() -> str:
    path = getattr(settings, "FRONTEND_REFERRAL_PATH", "/referrals")
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


def send_referral_signup_email(referrer, referred):
    return send_email(
        to=referrer.email,
        subject="Someone you referred just joined SolvexMarketInc",
        template="referral/referral_signup.html",
        context={
            "first_name": _first_name(referrer),
            "referred_name": mask_name(referred.full_name),
            "bonus_amount": money(settings.REFERRAL_BONUS_USD),
            "dashboard_url": _dashboard_url(),
        },
        from_email=settings.NOTICE_FROM_EMAIL,
    )


def send_referral_bonus_email(referrer, referral):
    total = (
        Referral.objects.filter(referrer=referrer, status=Referral.Status.REWARDED)
        .aggregate(total=Sum("bonus_amount"))["total"]
        or Decimal("0")
    )
    return send_email(
        to=referrer.email,
        subject="You earned a SolvexMarketInc referral bonus",
        template="referral/referral_bonus.html",
        context={
            "first_name": _first_name(referrer),
            "referred_name": mask_name(referral.referred.full_name),
            "bonus_amount": money(referral.bonus_amount or settings.REFERRAL_BONUS_USD),
            "total_earned": money(total),
            "dashboard_url": _dashboard_url(),
            "show_risk_footer": True,
        },
        from_email=settings.NOTICE_FROM_EMAIL,
    )
