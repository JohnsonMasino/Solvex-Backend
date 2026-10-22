from django.conf import settings
from django.db import migrations


def create_missing_profiles(apps, schema_editor):
    """Give every pre-existing user an (empty) profile."""
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Profile = apps.get_model("profiles", "Profile")
    missing = [Profile(user_id=pk) for pk in User.objects.filter(profile__isnull=True).values_list("pk", flat=True)]
    Profile.objects.bulk_create(missing, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
