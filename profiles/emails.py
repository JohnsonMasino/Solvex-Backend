from django.conf import settings
from django.utils import timezone

from core.mailer import send_email


def send_profile_updated_email(user, changed_fields):
    """`changed_fields` = human labels only (e.g. ["Phone number", "Address"]); never old/new values."""
    return send_email(
        to=user.email,
        subject="Your SolvexMarketInc profile was updated",
        template="profiles/profile_updated.html",
        context={"user": user, "changed_fields": list(changed_fields), "timestamp": timezone.now()},
        from_email=settings.NOTICE_FROM_EMAIL,
    )
