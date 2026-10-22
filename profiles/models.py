from django.conf import settings
from django.db import models

from core.models import TimeStampedModel, UUIDModel


class Profile(UUIDModel, TimeStampedModel):
    class Sex(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    class EntryMethod(models.TextChoices):
        AUTOCOMPLETE = "autocomplete", "Autocomplete"
        MANUAL = "manual", "Manual"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="profile", on_delete=models.CASCADE)
    date_of_birth = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=10, choices=Sex.choices, blank=True)
    nationality = models.CharField(max_length=2, blank=True)  # ISO 3166-1 alpha-2
    phone_number = models.CharField(max_length=20, blank=True)  # E.164
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)  # state / region / province
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True)  # ISO alpha-2
    address_entry_method = models.CharField(max_length=12, choices=EntryMethod.choices, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Profile({self.user_id})"

    @property
    def has_full_address(self) -> bool:
        return bool(self.address_line1 and self.city and self.country)

    @property
    def is_complete(self) -> bool:
        return bool(
            self.date_of_birth and self.sex and self.nationality and self.phone_number and self.has_full_address
        )
