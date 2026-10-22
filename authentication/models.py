from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel, UUIDModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email=(email or "").strip().lower())

    def _create_user(self, email, password, full_name, **extra_fields):
        if not email:
            raise ValueError("The email address is required.")
        email = email.strip().lower()
        user = self.model(email=email, full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, full_name="", **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, full_name, **extra_fields)

    def create_superuser(self, email, password=None, full_name="", **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_email_verified", True)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        # tier stays 1: superusers only use the Django admin.
        return self._create_user(email, password, full_name, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin, UUIDModel, TimeStampedModel):
    class KYCStatus(models.TextChoices):
        NOT_SUBMITTED = "not_submitted", "Not submitted"
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    TIER_CHOICES = ((1, "Tier 1"), (2, "Tier 2"))

    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=150)
    is_email_verified = models.BooleanField(default=False)
    tier = models.PositiveSmallIntegerField(choices=TIER_CHOICES, default=1)  # written by kyc only
    kyc_status = models.CharField(
        max_length=20, choices=KYCStatus.choices, default=KYCStatus.NOT_SUBMITTED
    )  # written by kyc only
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ("-date_joined",)

    def save(self, *args, **kwargs):
        # Always store the email lowercased + stripped (case-insensitive uniqueness).
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class EmailOTP(UUIDModel, TimeStampedModel):
    class Purpose(models.TextChoices):
        REGISTRATION = "registration", "Registration"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_otps")
    purpose = models.CharField(max_length=30, choices=Purpose.choices, default=Purpose.REGISTRATION)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    last_sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "purpose", "is_used"])]

    def __str__(self):
        return f"OTP({self.user_id}, {self.purpose})"


class PasswordResetToken(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"ResetToken({self.user_id})"
