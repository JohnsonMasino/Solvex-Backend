"""
profiles/services.py
Public cross-app contract (General Guide section 8):
    get_or_create_profile(user) -> Profile
    sync_from_kyc(user, kyc_data) -> None      # called by the kyc app on approval
Internal (used by the view):
    update_profile(user, validated_data) -> (Profile, list[str])
"""
import logging

import pycountry
from django.conf import settings
from django.db import transaction

from core.exceptions import BusinessError

from . import emails
from .models import Profile

logger = logging.getLogger(__name__)

IDENTITY_FIELDS = ("full_name", "date_of_birth", "nationality", "sex")
LOCKED_MESSAGE = (
    "These details were verified during KYC and can no longer be edited. Contact support to request a change."
)
FIELD_LABELS = {  # order = order used in the notification email
    "full_name": "Full name",
    "date_of_birth": "Date of birth",
    "sex": "Sex",
    "nationality": "Nationality",
    "phone_number": "Phone number",
    "address": "Address",
}
SIMPLE_PROFILE_FIELDS = ("date_of_birth", "sex", "nationality", "phone_number")
# API address key -> Profile model attribute
ADDRESS_MAP = {
    "line1": "address_line1",
    "line2": "address_line2",
    "city": "city",
    "state": "state",
    "postal_code": "postal_code",
    "country": "country",
    "entry_method": "address_entry_method",
}
ADDRESS_CORE_KEYS = ("line1", "line2", "city", "state", "postal_code", "country")
# keys the kyc app sends to sync_from_kyc (besides full_name)
KYC_PROFILE_KEYS = (
    "date_of_birth", "sex", "nationality", "phone_number",
    "address_line1", "address_line2", "city", "state", "postal_code", "country", "address_entry_method",
)


# --------------------------------------------------------------------------- country helpers
def _lookup_country(code: str):
    code = (code or "").strip()
    if len(code) != 2 or not code.isalpha():
        return None
    try:
        return pycountry.countries.get(alpha_2=code.upper())
    except (KeyError, LookupError):
        return None


def is_valid_country(code: str) -> bool:
    return _lookup_country(code) is not None


def country_name(code: str) -> str:
    country = _lookup_country(code)
    if country is None:
        return ""
    return getattr(country, "common_name", None) or country.name


# --------------------------------------------------------------------------- identity lock
def is_identity_locked(user) -> bool:
    return bool(getattr(settings, "PROFILE_LOCK_IDENTITY_AFTER_KYC", True)) and user.kyc_status == "approved"


def get_locked_fields(user) -> list:
    return list(IDENTITY_FIELDS) if is_identity_locked(user) else []


# --------------------------------------------------------------------------- public contract
def get_or_create_profile(user) -> Profile:
    profile, _ = Profile.objects.select_related("user").get_or_create(user=user)
    return profile


def sync_from_kyc(user, kyc_data: dict) -> None:
    """
    Called by the kyc app on approval (inside its transaction).
    Bypasses the identity lock and overwrites values. Missing keys are skipped,
    unknown keys ignored. Never sends emails.
    """
    profile = get_or_create_profile(user)
    for key in KYC_PROFILE_KEYS:
        if key not in kyc_data:
            continue
        value = kyc_data[key]
        if key in ("nationality", "country") and value:
            value = str(value).upper()
        if value is None and key != "date_of_birth":
            value = ""
        setattr(profile, key, value)
    profile.save()

    full_name = kyc_data.get("full_name")
    if full_name:
        user.full_name = full_name
        user.save(update_fields=["full_name", "updated_at"])


# --------------------------------------------------------------------------- update
def _address_errors(merged: dict) -> dict:
    """Merged address must be completely empty, or contain line1, city, country (+ entry_method)."""
    if not any((merged.get(k) or "").strip() for k in ADDRESS_CORE_KEYS):
        return {}
    errors = {}
    for key in ("line1", "city", "country"):
        if not merged.get(key):
            errors[f"address.{key}"] = ["This field is required when providing an address."]
    if not merged.get("entry_method"):
        errors["address.entry_method"] = ["This field is required when providing an address."]
    return errors


def update_profile(user, validated_data: dict):
    """
    Applies a validated PATCH. Returns (profile, changed_labels).
    `changed_labels` is empty when nothing actually changed (no email, no writes).
    """
    with transaction.atomic():
        db_user = type(user).objects.select_for_update().get(pk=user.pk)
        profile, _ = Profile.objects.select_for_update().get_or_create(user=db_user)
        profile.user = db_user

        current = {
            "full_name": db_user.full_name,
            "date_of_birth": profile.date_of_birth,
            "sex": profile.sex,
            "nationality": profile.nationality,
            "phone_number": profile.phone_number,
        }

        # 1. identity lock (same value = silently accepted no-op)
        if is_identity_locked(db_user):
            violations = {
                f: ["This field is locked after identity verification."]
                for f in IDENTITY_FIELDS
                if f in validated_data and validated_data[f] != current[f]
            }
            if violations:
                raise BusinessError(
                    "field_locked", LOCKED_MESSAGE, status=403,
                    errors=violations, data={"locked_fields": list(IDENTITY_FIELDS)},
                )

        # 2. work out what really changes
        changed = set()
        for field in ("full_name",) + SIMPLE_PROFILE_FIELDS:
            if field in validated_data and validated_data[field] != current[field]:
                changed.add(field)

        new_address = None
        address_patch = validated_data.get("address")
        if address_patch:
            current_address = {k: getattr(profile, attr) for k, attr in ADDRESS_MAP.items()}
            merged = {**current_address, **address_patch}
            errors = _address_errors(merged)
            if errors:
                raise BusinessError(
                    "validation_error", "Please correct the highlighted fields.", status=400, errors=errors
                )
            if not any((merged.get(k) or "").strip() for k in ADDRESS_CORE_KEYS):
                merged["entry_method"] = ""  # fully cleared address
            if merged != current_address:
                changed.add("address")
                new_address = merged

        if not changed:
            return profile, []

        # 3. write
        if "full_name" in changed:
            db_user.full_name = validated_data["full_name"]
            db_user.save(update_fields=["full_name", "updated_at"])
        for field in SIMPLE_PROFILE_FIELDS:
            if field in changed:
                setattr(profile, field, validated_data[field])
        if new_address is not None:
            for key, attr in ADDRESS_MAP.items():
                setattr(profile, attr, new_address[key])
        profile.save()

        labels = [label for field, label in FIELD_LABELS.items() if field in changed]
        transaction.on_commit(lambda: emails.send_profile_updated_email(db_user, labels))
        return profile, labels
