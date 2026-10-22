import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="ReferralProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=8, unique=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="referral_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="Referral",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code_used", models.CharField(max_length=8)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("rewarded", "Rewarded")], db_index=True, default="pending", max_length=10)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("rewarded_at", models.DateTimeField(blank=True, null=True)),
                ("bonus_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ("wallet_reference", models.CharField(blank=True, default="", max_length=80)),
                ("referred", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="referred_by", to=settings.AUTH_USER_MODEL)),
                ("referrer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referrals_made", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="referral",
            index=models.Index(fields=["referrer", "status"], name="referral_ref_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="referral",
            constraint=models.CheckConstraint(condition=~Q(referrer=F("referred")), name="referral_no_self_referral"),
        ),
    ]
