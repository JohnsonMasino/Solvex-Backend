"""
Backfill a Wallet for every user that existed before the wallet app was installed.

HOW TO USE: run `python manage.py makemigrations wallet` first (creates 0001_initial),
then copy THIS file into wallet/migrations/ and run `python manage.py migrate`.
"""
from django.conf import settings
from django.db import migrations


def backfill_wallets(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    Wallet = apps.get_model("wallet", "Wallet")
    have_wallet = set(Wallet.objects.values_list("user_id", flat=True))
    missing = [
        Wallet(user_id=pk)
        for pk in User.objects.exclude(pk__in=have_wallet).values_list("pk", flat=True)
    ]
    Wallet.objects.bulk_create(missing, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("wallet", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(backfill_wallets, migrations.RunPython.noop),
    ]
