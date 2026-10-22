from django.apps import AppConfig


class WalletConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wallet"
    label = "wallet"
    verbose_name = "Wallet"

    def ready(self):
        from . import signals  # noqa: F401  (connects the post_save receiver)
