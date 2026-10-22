from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel, UUIDModel
from core.utils import mask_tail


# --------------------------------------------------------------------------- #
# Choices (stored as the key; labels are returned by GET /api/kyc/options/)
# --------------------------------------------------------------------------- #
class KYCStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Sex(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"


class AddressEntryMethod(models.TextChoices):
    AUTOCOMPLETE = "autocomplete", "Autocomplete"
    MANUAL = "manual", "Manual"


class IDType(models.TextChoices):
    PASSPORT = "passport", "Passport"
    NATIONAL_ID = "national_id", "National ID card"
    DRIVERS_LICENSE = "drivers_license", "Driver's license"
    RESIDENCE_PERMIT = "residence_permit", "Residence permit"
    VOTERS_CARD = "voters_card", "Voter's card"


class EmploymentStatus(models.TextChoices):
    EMPLOYED = "employed", "Employed"
    SELF_EMPLOYED = "self_employed", "Self-employed"
    STUDENT = "student", "Student"
    RETIRED = "retired", "Retired"
    UNEMPLOYED = "unemployed", "Unemployed"
    OTHER = "other", "Other"


class SourceOfIncome(models.TextChoices):
    SALARY = "salary", "Salary"
    BUSINESS = "business", "Business income"
    INVESTMENTS = "investments", "Investments"
    SAVINGS = "savings", "Savings"
    INHERITANCE = "inheritance", "Inheritance"
    GIFT = "gift", "Gift"
    OTHER = "other", "Other"


class AnnualIncomeRange(models.TextChoices):
    UNDER_10K = "under_10k", "Under $10,000"
    R_10K_25K = "10k_25k", "$10,000 – $25,000"
    R_25K_50K = "25k_50k", "$25,000 – $50,000"
    R_50K_100K = "50k_100k", "$50,000 – $100,000"
    R_100K_250K = "100k_250k", "$100,000 – $250,000"
    R_250K_PLUS = "250k_plus", "$250,000+"


class ExpectedAnnualVolume(models.TextChoices):
    UNDER_5K = "under_5k", "Under $5,000"
    R_5K_25K = "5k_25k", "$5,000 – $25,000"
    R_25K_100K = "25k_100k", "$25,000 – $100,000"
    R_100K_500K = "100k_500k", "$100,000 – $500,000"
    R_500K_PLUS = "500k_plus", "$500,000+"


class TransactionFrequency(models.TextChoices):
    OCCASIONALLY = "occasionally", "Occasionally"
    MONTHLY = "monthly", "Monthly"
    WEEKLY = "weekly", "Weekly"
    DAILY = "daily", "Daily"


class TypicalTransactionSize(models.TextChoices):
    UNDER_500 = "under_500", "Under $500"
    R_500_2K = "500_2k", "$500 – $2,000"
    R_2K_10K = "2k_10k", "$2,000 – $10,000"
    R_10K_50K = "10k_50k", "$10,000 – $50,000"
    R_50K_PLUS = "50k_plus", "$50,000+"


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
class KYCSubmission(UUIDModel, TimeStampedModel):
    # ---- meta -------------------------------------------------------------
    # PROTECT: compliance records must never disappear because a user row was deleted.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="kyc_submissions"
    )
    attempt_number = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=10, choices=KYCStatus.choices, default=KYCStatus.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, help_text="Shown to the user when rejected.")
    admin_note = models.TextField(blank=True, help_text="Internal only. Never exposed in the API.")

    # ---- personal ---------------------------------------------------------
    full_name = models.CharField(max_length=150)
    date_of_birth = models.DateField()
    nationality = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    sex = models.CharField(max_length=10, choices=Sex.choices)
    phone_number = models.CharField(max_length=20, help_text="E.164, e.g. +2348012345678")

    # ---- address ----------------------------------------------------------
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    address_entry_method = models.CharField(max_length=15, choices=AddressEntryMethod.choices)

    # ---- identification ---------------------------------------------------
    id_type = models.CharField(max_length=20, choices=IDType.choices)
    id_number = models.CharField(max_length=30, help_text="Normalised: uppercase, no spaces.")
    id_number_hash = models.CharField(max_length=64, db_index=True)

    # ---- employment -------------------------------------------------------
    employment_status = models.CharField(max_length=15, choices=EmploymentStatus.choices)
    source_of_income = models.CharField(max_length=15, choices=SourceOfIncome.choices)
    occupation = models.CharField(max_length=120)
    employer_name = models.CharField(max_length=150, blank=True)
    annual_income_range = models.CharField(max_length=15, choices=AnnualIncomeRange.choices)
    expected_annual_volume = models.CharField(max_length=15, choices=ExpectedAnnualVolume.choices)
    transaction_frequency = models.CharField(max_length=15, choices=TransactionFrequency.choices)
    typical_transaction_size = models.CharField(max_length=15, choices=TypicalTransactionSize.choices)

    # ---- consent ----------------------------------------------------------
    accuracy_declaration = models.BooleanField()
    privacy_notice_accepted = models.BooleanField()
    terms_accepted = models.BooleanField()
    marketing_consent = models.BooleanField(default=False)
    consent_at = models.DateTimeField()
    consent_ip = models.GenericIPAddressField(null=True, blank=True)
    consent_user_agent = models.CharField(max_length=300, blank=True)
    policy_version = models.CharField(max_length=20)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "KYC submission"
        verbose_name_plural = "KYC submissions"
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status__in=["pending", "approved"]),
                name="kyc_one_active_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="kyc_status_created_idx"),
        ]

    def __str__(self):
        return f"KYC #{self.attempt_number} - {self.user_id} ({self.status})"

    @property
    def masked_id_number(self) -> str:
        return mask_tail(self.id_number, keep=4)
