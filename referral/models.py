from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.models import TimeStampedModel, UUIDModel


class ReferralProfile(UUIDModel, TimeStampedModel):
    """Exactly one unique referral code per user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_profile"
    )
    # unique=True already creates an index (satisfies "unique, db_index")
    code = models.CharField(max_length=8, unique=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.code} ({self.user_id})"


class Referral(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REWARDED = "rewarded", "Rewarded"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals_made"
    )
    # A user can only ever have one referrer
    referred = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referred_by"
    )
    code_used = models.CharField(max_length=8)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rewarded_at = models.DateTimeField(null=True, blank=True)
    bonus_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    wallet_reference = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["referrer", "status"], name="referral_ref_status_idx")]
        constraints = [
            # `condition=` is Django 5.1+. On Django < 5.1 use `check=` instead.
            models.CheckConstraint(
                condition=~Q(referrer=F("referred")), name="referral_no_self_referral"
            )
        ]

    def __str__(self):
        return f"{self.referrer_id} -> {self.referred_id} [{self.status}]"
