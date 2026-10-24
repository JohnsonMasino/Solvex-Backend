from django.db import migrations
from django.db.models import F


def backfill_total(apps, schema_editor):
    """Old withdrawals had no fee: total_usd == usd_amount."""
    Withdrawal = apps.get_model("wallet", "Withdrawal")
    Withdrawal.objects.update(total_usd=F("usd_amount"))


class Migration(migrations.Migration):
    dependencies = [
        ("wallet", "0002_multi_asset_and_withdrawal_fee"),
    ]

    operations = [
        migrations.RunPython(backfill_total, migrations.RunPython.noop),
    ]