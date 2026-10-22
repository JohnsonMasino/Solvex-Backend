from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from referral.services import get_or_create_referral_profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="referral_create_profile")
def create_referral_profile(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        get_or_create_referral_profile(instance)
