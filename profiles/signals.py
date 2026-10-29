from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="profiles_create_profile")
def create_profile_for_new_user(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(post_delete, sender=Profile, dispatch_uid="profiles_delete_avatar")
def delete_avatar_with_profile(sender, instance, **kwargs):
    """When a profile row is deleted, remove its picture from Cloudinary too (no orphan images)."""
    public_id = instance.profile_photo_public_id
    if not public_id:
        return
    from . import services  # lazy: avoids an import cycle at app start

    transaction.on_commit(lambda: services.delete_avatar_image(public_id))