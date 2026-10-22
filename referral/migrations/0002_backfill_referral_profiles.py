"""Data migration: give every pre-existing user a unique referral code."""
import secrets

from django.conf import settings
from django.db import migrations

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LENGTH = 8


def backfill(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    ReferralProfile = apps.get_model("referral", "ReferralProfile")

    used_codes = set(ReferralProfile.objects.values_list("code", flat=True))
    users_with_profile = set(ReferralProfile.objects.values_list("user_id", flat=True))

    for user in User.objects.exclude(pk__in=users_with_profile).iterator():
        for _ in range(20):
            code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
            if code not in used_codes:
                break
        else:
            raise RuntimeError("Could not generate a unique referral code")
        used_codes.add(code)
        ReferralProfile.objects.create(user=user, code=code)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("referral", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
