import re
from collections.abc import Mapping
from datetime import date
from typing import Optional

from django.utils import timezone
from rest_framework import serializers
from rest_framework.fields import empty

from core import images
from core.exceptions import BusinessError

from . import services
from .models import Profile

# Fields that must produce a clear error when sent to PATCH /profile/ (instead of being silently ignored).
READ_ONLY_FIELDS = (
    "email", "tier", "kyc_status", "id", "date_joined", "is_email_verified",
    "avatar_url", "profile_photo",  # the picture is changed with POST /profile/photo/
)
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


# --------------------------------------------------------------------------- field helpers
class OptionalTextField(serializers.CharField):
    """Optional text: '' clears the value, but whitespace-only input is rejected."""

    def __init__(self, **kwargs):
        kwargs.setdefault("allow_blank", True)
        super().__init__(**kwargs)

    def run_validation(self, data=empty):
        if isinstance(data, str) and data != "" and not data.strip():
            raise serializers.ValidationError("This field may not contain only whitespace.")
        return super().run_validation(data)


def _clean_country(value: str) -> str:
    value = (value or "").strip().upper()
    if not services.is_valid_country(value):
        raise serializers.ValidationError("Enter a valid ISO 3166-1 alpha-2 country code (e.g. NG).")
    return value


# --------------------------------------------------------------------------- input
class AddressInputSerializer(serializers.Serializer):
    """Partial address: only the keys you send change."""
    line1 = serializers.CharField(max_length=255, required=False)
    line2 = OptionalTextField(max_length=255, required=False)
    city = serializers.CharField(max_length=100, required=False)
    state = OptionalTextField(max_length=100, required=False)
    postal_code = OptionalTextField(max_length=20, required=False)
    country = serializers.CharField(required=False)
    entry_method = serializers.ChoiceField(choices=Profile.EntryMethod.choices, required=False)

    def validate_country(self, value):
        return _clean_country(value)


class ProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150, required=False)
    date_of_birth = serializers.DateField(input_formats=["iso-8601"], required=False)
    sex = serializers.ChoiceField(choices=Profile.Sex.choices, required=False)
    nationality = serializers.CharField(required=False)
    phone_number = OptionalTextField(max_length=20, required=False)
    address = AddressInputSerializer(required=False)

    def to_internal_value(self, data):
        # Read-only account fields must produce a clear error rather than being silently ignored.
        ro_errors = {}
        if isinstance(data, Mapping):
            ro_errors = {f: ["This field cannot be changed here."] for f in READ_ONLY_FIELDS if f in data}
        try:
            validated = super().to_internal_value(data)
        except serializers.ValidationError as exc:
            detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
            detail.update(ro_errors)
            raise serializers.ValidationError(detail)
        if ro_errors:
            raise serializers.ValidationError(ro_errors)
        return validated

    def validate_full_name(self, value):
        value = re.sub(r"\s+", " ", value).strip()
        if not 2 <= len(value) <= 150:
            raise serializers.ValidationError("Full name must be between 2 and 150 characters.")
        if not any(c.isalpha() for c in value) or any(not (c.isalpha() or c in " -'.\u2019") for c in value):
            raise serializers.ValidationError(
                "Full name may only contain letters, spaces, hyphens, apostrophes and dots."
            )
        return value

    def validate_date_of_birth(self, value: date):
        today = timezone.now().date()
        if value > today:
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if age < 18:
            raise serializers.ValidationError("You must be at least 18 years old.")
        if age > 120:
            raise serializers.ValidationError("Enter a valid date of birth.")
        return value

    def validate_nationality(self, value):
        return _clean_country(value)

    def validate_phone_number(self, value):
        if value and not PHONE_RE.match(value):
            raise serializers.ValidationError(
                "Enter the phone number in international format, e.g. +2348012345678."
            )
        return value

    def validate(self, attrs):
        if attrs.get("address") == {}:
            attrs.pop("address")
        if not attrs:
            raise serializers.ValidationError({"non_field_errors": ["Provide at least one field to update."]})
        return attrs


class ProfilePhotoSerializer(serializers.Serializer):
    """multipart/form-data with one file field named `profile_photo`."""
    profile_photo = serializers.FileField(
        allow_empty_file=False,
        error_messages={
            "required": "Please choose a profile picture.",
            "null": "Please choose a profile picture.",
            "empty": "The selected file is empty.",
            "invalid": "Upload the image as a file (multipart/form-data).",
            "no_name": "The selected file has no name.",
        },
    )

    def validate_profile_photo(self, value):
        # Checks the REAL file content (type, size) so the error lands on the right field.
        # The file is rewound because the service reads it again before uploading.
        try:
            value.seek(0)
            images.read_and_validate(value)
        except BusinessError as exc:
            raise serializers.ValidationError(getattr(exc, "message", None) or str(exc))
        finally:
            try:
                value.seek(0)
            except Exception:
                pass
        return value


# --------------------------------------------------------------------------- output
class AddressOutputSerializer(serializers.Serializer):
    line1 = serializers.CharField(source="address_line1")
    line2 = serializers.CharField(source="address_line2")
    city = serializers.CharField()
    state = serializers.CharField()
    postal_code = serializers.CharField()
    country = serializers.CharField()
    country_name = serializers.SerializerMethodField()
    entry_method = serializers.CharField(source="address_entry_method")

    def get_country_name(self, obj) -> str:
        return services.country_name(obj.country)


class ProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    nationality_name = serializers.SerializerMethodField()
    address = AddressOutputSerializer(source="*", read_only=True)
    tier = serializers.IntegerField(source="user.tier", read_only=True)
    kyc_status = serializers.CharField(source="user.kyc_status", read_only=True)
    identity_locked = serializers.SerializerMethodField()
    locked_fields = serializers.SerializerMethodField()
    is_complete = serializers.BooleanField(read_only=True)
    member_since = serializers.DateTimeField(source="user.date_joined", read_only=True)

    class Meta:
        model = Profile
        fields = (
            "email", "full_name", "avatar_url", "date_of_birth", "sex", "nationality", "nationality_name",
            "phone_number", "address", "tier", "kyc_status", "identity_locked", "locked_fields",
            "is_complete", "member_since", "updated_at",
        )
        read_only_fields = fields

    def get_avatar_url(self, obj) -> Optional[str]:
        return services.avatar_url_for(obj)

    def get_nationality_name(self, obj) -> str:
        return services.country_name(obj.nationality)

    def get_identity_locked(self, obj) -> bool:
        return services.is_identity_locked(obj.user)

    def get_locked_fields(self, obj) -> list[str]:
        return services.get_locked_fields(obj.user)