"""
Field validators for the KYC app.

The personal / address rules mirror the profiles app (03 section 3). They are copied
here on purpose: apps may not import each other except through the service contracts.
"""
import re
from datetime import date

import pycountry
from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '’.\-]+[^\W\d_]+)*\.?$", re.UNICODE)
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_ID_RE = re.compile(r"^[A-Z0-9]{4,30}$")
MAX_AGE = 120


def _country_exists(code: str) -> bool:
    try:
        return pycountry.countries.get(alpha_2=code) is not None
    except (KeyError, LookupError):
        return False


def validate_person_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) < 2:
        raise serializers.ValidationError("Enter your full legal name.")
    if not _NAME_RE.match(value):
        raise serializers.ValidationError(
            "Name may only contain letters, spaces, hyphens, apostrophes and full stops."
        )
    return value


def validate_birth_date(value: date) -> date:
    today = timezone.now().date()
    if value > today:
        raise serializers.ValidationError("Date of birth cannot be in the future.")
    age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    if age > MAX_AGE:
        raise serializers.ValidationError("Enter a valid date of birth.")
    if age < settings.KYC_MIN_AGE:
        raise serializers.ValidationError(
            f"You must be at least {settings.KYC_MIN_AGE} years old."
        )
    return value


def validate_country_code(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 2 or not _country_exists(value):
        raise serializers.ValidationError("Enter a valid ISO 3166-1 alpha-2 country code (e.g. NG).")
    return value


def validate_phone(value: str) -> str:
    cleaned = re.sub(r"[\s\-().]", "", value)
    if not _PHONE_RE.match(cleaned):
        raise serializers.ValidationError(
            "Enter a valid phone number in international format, e.g. +2348012345678."
        )
    return cleaned


def normalize_id_number(value: str) -> str:
    return re.sub(r"[\s\-]", "", value or "").upper()


def validate_id_number(value: str) -> str:
    cleaned = normalize_id_number(value)
    if not _ID_RE.match(cleaned):
        raise serializers.ValidationError(
            "ID number must be 4–30 letters or digits (spaces and hyphens are ignored)."
        )
    return cleaned
