from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel, UUIDModel
from wallet.models import generate_reference


# --------------------------------------------------------------------------- #
# Choices
# --------------------------------------------------------------------------- #
class LoanDuration(models.TextChoices):
    WEEK_1 = "1_week", "1 week"
    WEEKS_2 = "2_weeks", "2 weeks"
    MONTH_1 = "1_month", "1 month"
    MONTHS_2 = "2_months", "2 months"
    MONTHS_3 = "3_months", "3 months"


# A "month" is a flat 30 days so due dates are predictable.
DURATION_DAYS = {
    LoanDuration.WEEK_1: 7,
    LoanDuration.WEEKS_2: 14,
    LoanDuration.MONTH_1: 30,
    LoanDuration.MONTHS_2: 60,
    LoanDuration.MONTHS_3: 90,
}


class LoanStatus(models.TextChoices):
    PENDING = "pending", "Pending approval"
    ACTIVE = "active", "Active"
    REPAID = "repaid", "Repaid"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


# A user may have only ONE loan in these states at any time.
OPEN_STATUSES = (LoanStatus.PENDING, LoanStatus.ACTIVE)


class RepaymentSource(models.TextChoices):
    DEPOSIT = "deposit", "Automatic (from a deposit)"
    WALLET = "wallet", "Manual (from wallet balance)"


# --------------------------------------------------------------------------- #
# Loan
# --------------------------------------------------------------------------- #
class Loan(UUIDModel, TimeStampedModel):
    """
    Lifecycle:  pending --(admin approves)--> active --(fully paid)--> repaid
                pending --(admin rejects)---> rejected
                pending --(user cancels)----> cancelled

    The rates are frozen when the user submits the request (what they saw on screen).
    The clock (started_at / halfway_at / due_at) starts when the ADMIN APPROVES.
    Interest is NOT stored: it is computed from the timestamps (see calculations.py).
    """

    Status = LoanStatus

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="loans")
    reference = models.CharField(max_length=20, unique=True)
    principal = models.DecimalField(max_digits=18, decimal_places=2, help_text="Amount requested / borrowed.")
    duration = models.CharField(max_length=10, choices=LoanDuration.choices)
    duration_days = models.PositiveSmallIntegerField()
    daily_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Daily interest on the principal while within the term (e.g. 0.20)."
    )
    overdue_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Daily interest on the principal after the due date (e.g. 1.00)."
    )
    amount_repaid = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=LoanStatus.choices, default=LoanStatus.PENDING, db_index=True)
    credited_to_wallet = models.BooleanField(
        default=False, help_text="True if the principal was added to the wallet balance when approved."
    )

    # Set on approval.
    started_at = models.DateTimeField(null=True, blank=True, help_text="Approval time; interest starts here.")
    halfway_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # Admin processing (same shape as Deposit).
    decline_reason = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    halfway_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    overdue_notice_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # Race-safe backstop: ONE pending-or-active loan per user.
            models.UniqueConstraint(
                fields=["user"], condition=Q(status__in=["pending", "active"]), name="loan_one_open_per_user"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="loan_user_created_idx"),
            models.Index(fields=["status", "due_at"], name="loan_status_due_idx"),
            models.Index(fields=["status", "halfway_at"], name="loan_status_half_idx"),
        ]

    def __str__(self):
        return f"{self.reference} {self.principal} USD ({self.status})"


class LoanRepayment(UUIDModel, TimeStampedModel):
    """Append-only record of every payment made against a loan."""

    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="repayments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    source = models.CharField(max_length=10, choices=RepaymentSource.choices)
    reference = models.CharField(max_length=80, blank=True, help_text="e.g. deposit:<uuid> for automatic repayments.")
    outstanding_after = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["reference"], condition=~Q(reference=""), name="loan_repayment_unique_ref"
            ),
        ]

    def __str__(self):
        return f"{self.loan_id} -{self.amount} ({self.source})"