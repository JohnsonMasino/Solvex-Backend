from django.apps import AppConfig


class ReferralConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"  # unused: all models use UUID pks
    name = "referral"
    verbose_name = "Referral"

    def ready(self):
        from referral import signals  # noqa: F401  (connects post_save handler)
