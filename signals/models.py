from django.db import models


class EligibleEmail(models.Model):
    """
    One row = one email address allowed to activate a trading signal.

    Fully managed from the admin console (add / edit / pause / delete).
    A user is eligible when their account email matches an ACTIVE row here.
    """

    email = models.EmailField(
        unique=True,
        help_text="Must match the email the user registered with. Stored in lowercase.",
    )
    note = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional internal note (e.g. who approved this user). Never shown to users.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to temporarily remove eligibility without deleting the email.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Eligible email"
        verbose_name_plural = "Eligible emails"

    def __str__(self):
        return self.email

    @staticmethod
    def normalize(value):
        return (value or "").strip().lower()

    def clean(self):
        # Runs before validate_unique(), so "JANE@x.com" and "jane@x.com"
        # are correctly caught as duplicates in the admin form.
        self.email = self.normalize(self.email)

    def save(self, *args, **kwargs):
        # Also normalise for shell / script / bulk-script inserts.
        self.email = self.normalize(self.email)
        super().save(*args, **kwargs)


class PurchasedEmail(models.Model):
    """
    One row = one email address that has purchased a trading signal.

    Fully managed from the admin console (add / edit / pause / delete).
    The frontend reads the ACTIVE rows through GET /api/signals/purchases/.
    """

    email = models.EmailField(
        unique=True,
        help_text="Must match the email the user registered with. Stored in lowercase.",
    )
    note = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional internal note (e.g. plan bought, payment reference). Never shown to users.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to hide this purchase from the frontend without deleting the email.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Purchased email"
        verbose_name_plural = "Purchased emails"

    def __str__(self):
        return self.email

    @staticmethod
    def normalize(value):
        return (value or "").strip().lower()

    def clean(self):
        self.email = self.normalize(self.email)

    def save(self, *args, **kwargs):
        self.email = self.normalize(self.email)
        super().save(*args, **kwargs)