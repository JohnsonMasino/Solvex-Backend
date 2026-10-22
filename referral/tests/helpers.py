"""Test helpers. If your custom User manager's create_user signature differs, adjust make_user()."""
from django.contrib.auth import get_user_model


def make_user(email, full_name="Test User", verified=True, active=True):
    User = get_user_model()
    user = User.objects.create_user(email=email, password="StrongPass123!", full_name=full_name)
    User.objects.filter(pk=user.pk).update(is_email_verified=verified, is_active=active)
    user.refresh_from_db()
    return user
