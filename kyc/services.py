"""
kyc/services.py - ALL business logic + the public cross-app contract:

    submit_kyc(user, data, request) -> KYCSubmission
    approve_submission(submission, admin_user) -> None
    reject_submission(submission, admin_user, reason) -> None

This app is the ONLY writer of user.tier and user.kyc_status.
"""
import ipaddress
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import BusinessError
from core.utils import get_client_ip, hash_value

from . import emails
from .models import (
    AddressEntryMethod, AnnualIncomeRange, EmploymentStatus, ExpectedAnnualVolume,
    IDType, KYCStatus, KYCSubmission, Sex, SourceOfIncome, TransactionFrequency,
    TypicalTransactionSize,
)
from .validators import normalize_id_number

logger = logging.getLogger(__name__)

NOT_SUBMITTED = "not_submitted"


# --------------------------------------------------------------------------- #
# Read helpers (used by the views)
# --------------------------------------------------------------------------- #
def _opts(choices):
    return [{"value": value, "label": str(label)} for value, label in choices.choices]


def get_form_options() -> dict:
    return {
        "sex": _opts(Sex),
        "id_types": _opts(IDType),
        "employment_statuses": _opts(EmploymentStatus),
        "sources_of_income": _opts(SourceOfIncome),
        "income_ranges": _opts(AnnualIncomeRange),
        "annual_volume_ranges": _opts(ExpectedAnnualVolume),
        "transaction_frequencies": _opts(TransactionFrequency),
        "transaction_sizes": _opts(TypicalTransactionSize),
        "address_entry_methods": _opts(AddressEntryMethod),
        "min_age": settings.KYC_MIN_AGE,
        "policy_version": settings.KYC_POLICY_VERSION,
    }


def get_status_summary(user) -> dict:
    used = KYCSubmission.objects.filter(user=user).count()
    latest = KYCSubmission.objects.filter(user=user).order_by("-attempt_number", "-created_at").first()
    remaining = max(0, settings.KYC_MAX_ATTEMPTS - used)
    can_submit = user.kyc_status in (NOT_SUBMITTED, KYCStatus.REJECTED) and remaining > 0
    return {
        "tier": user.tier,
        "kyc_status": user.kyc_status,
        "can_submit": can_submit,
        "attempts_used": used,
        "attempts_remaining": remaining,
        "latest": latest,
    }


def get_prefill_data(user) -> dict:
    """Prefill from the profile (via the profiles service contract) + user.full_name."""
    profile = None
    try:
        from profiles.services import get_or_create_profile  # lazy: avoid circular imports
        profile = get_or_create_profile(user)
    except Exception:  # never let a prefill problem break the form
        logger.exception("kyc: could not load profile for prefill")

    def pick(name):
        value = getattr(profile, name, "") if profile is not None else ""
        if value is None:
            return ""
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    return {
        "personal": {
            "full_name": pick("full_name") or (getattr(user, "full_name", "") or ""),
            "date_of_birth": pick("date_of_birth"),
            "nationality": pick("nationality"),
            "sex": pick("sex"),
            "phone_number": pick("phone_number"),
        },
        "address": {
            "line1": pick("address_line1"),
            "line2": pick("address_line2"),
            "city": pick("city"),
            "state": pick("state"),
            "postal_code": pick("postal_code"),
            "country": pick("country"),
            "entry_method": pick("address_entry_method"),
        },
    }


# --------------------------------------------------------------------------- #
# Submit
# --------------------------------------------------------------------------- #
def _safe_ip(request):
    try:
        raw = get_client_ip(request)
        return str(ipaddress.ip_address(raw)) if raw else None
    except (ValueError, TypeError):
        return None


def _pending_error():
    return BusinessError(
        "kyc_already_pending", "Your verification is already under review.",
        status=409, data={"kyc_status": KYCStatus.PENDING},
    )


def _approved_error():
    return BusinessError(
        "kyc_already_approved", "Your identity is already verified.",
        status=409, data={"kyc_status": KYCStatus.APPROVED},
    )


def submit_kyc(user, data: dict, request) -> KYCSubmission:
    """`data` is the validated nested payload from SubmitKYCSerializer."""
    User = get_user_model()
    p, a = data["personal"], data["address"]
    i, e, c = data["identification"], data["employment"], data["consent"]

    id_number = normalize_id_number(i["id_number"])
    id_hash = hash_value(f"{i['id_type']}:{id_number}")

    with transaction.atomic():
        # Lock the user row: serialises concurrent submits from the same account.
        locked_user = User.objects.select_for_update().get(pk=user.pk)

        if locked_user.kyc_status == KYCStatus.PENDING:
            raise _pending_error()
        if locked_user.kyc_status == KYCStatus.APPROVED:
            raise _approved_error()

        existing = KYCSubmission.objects.filter(user=locked_user)
        active = existing.filter(status__in=[KYCStatus.PENDING, KYCStatus.APPROVED]).first()
        if active is not None:  # defensive: denormalised user.kyc_status was out of sync
            raise _pending_error() if active.status == KYCStatus.PENDING else _approved_error()

        used = existing.count()
        if used >= settings.KYC_MAX_ATTEMPTS:
            raise BusinessError(
                "kyc_attempts_exceeded",
                "You have reached the maximum number of verification attempts. Please contact support.",
                status=403,
                data={"attempts_used": used, "attempts_remaining": 0},
            )

        if (
            KYCSubmission.objects.filter(
                id_number_hash=id_hash, status__in=[KYCStatus.PENDING, KYCStatus.APPROVED]
            )
            .exclude(user=locked_user)
            .exists()
        ):
            raise BusinessError(
                "id_already_used",
                "This identification document can't be used. Please check the details or contact support.",
                status=409,
            )

        try:
            with transaction.atomic():  # savepoint so an IntegrityError doesn't poison the outer block
                submission = KYCSubmission.objects.create(
                    user=locked_user,
                    attempt_number=used + 1,
                    status=KYCStatus.PENDING,
                    # personal
                    full_name=p["full_name"],
                    date_of_birth=p["date_of_birth"],
                    nationality=p["nationality"],
                    sex=p["sex"],
                    phone_number=p["phone_number"],
                    # address
                    address_line1=a["line1"],
                    address_line2=a.get("line2", ""),
                    city=a["city"],
                    state=a.get("state", ""),
                    postal_code=a.get("postal_code", ""),
                    country=a["country"],
                    address_entry_method=a["entry_method"],
                    # identification
                    id_type=i["id_type"],
                    id_number=id_number,
                    id_number_hash=id_hash,
                    # employment
                    employment_status=e["employment_status"],
                    source_of_income=e["source_of_income"],
                    occupation=e["occupation"],
                    employer_name=e.get("employer_name", ""),
                    annual_income_range=e["annual_income_range"],
                    expected_annual_volume=e["expected_annual_volume"],
                    transaction_frequency=e["transaction_frequency"],
                    typical_transaction_size=e["typical_transaction_size"],
                    # consent
                    accuracy_declaration=c["accuracy_declaration"],
                    privacy_notice_accepted=c["privacy_notice_accepted"],
                    terms_accepted=c["terms_accepted"],
                    marketing_consent=c.get("marketing_consent", False),
                    consent_at=timezone.now(),
                    consent_ip=_safe_ip(request),
                    consent_user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:300],
                    policy_version=settings.KYC_POLICY_VERSION,
                )
        except IntegrityError:
            # kyc_one_active_per_user fired: a concurrent request won the race.
            raise _pending_error()

        locked_user.kyc_status = KYCStatus.PENDING
        locked_user.save(update_fields=["kyc_status"])

        transaction.on_commit(lambda: emails.send_kyc_submitted_email(locked_user, submission))
        if getattr(settings, "ADMIN_NOTIFICATION_EMAIL", ""):
            transaction.on_commit(lambda: emails.send_admin_new_kyc_email(submission))

    user.kyc_status = KYCStatus.PENDING  # keep the caller's in-memory user consistent
    return submission


# --------------------------------------------------------------------------- #
# Admin review
# --------------------------------------------------------------------------- #
def approve_submission(submission, admin_user) -> None:
    """Idempotent. Pending -> approved; user becomes Tier 2."""
    User = get_user_model()
    with transaction.atomic():
        locked = KYCSubmission.objects.select_for_update().get(pk=submission.pk)
        if locked.status != KYCStatus.PENDING:
            return
        user = User.objects.select_for_update().get(pk=locked.user_id)

        locked.status = KYCStatus.APPROVED
        locked.reviewed_by = admin_user
        locked.reviewed_at = timezone.now()
        locked.rejection_reason = ""
        locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])

        user.tier = 2
        user.kyc_status = KYCStatus.APPROVED
        user.save(update_fields=["tier", "kyc_status"])

        kyc_data = {
            "full_name": locked.full_name,
            "date_of_birth": locked.date_of_birth,
            "sex": locked.sex,
            "nationality": locked.nationality,
            "phone_number": locked.phone_number,
            "address_line1": locked.address_line1,
            "address_line2": locked.address_line2,
            "city": locked.city,
            "state": locked.state,
            "postal_code": locked.postal_code,
            "country": locked.country,
            "address_entry_method": locked.address_entry_method,
        }
        try:
            with transaction.atomic():  # savepoint: a profile problem must not undo the approval
                from profiles.services import sync_from_kyc  # lazy cross-app import
                sync_from_kyc(user, kyc_data)
        except Exception:
            logger.exception("kyc: profile sync failed for submission %s", locked.pk)

        transaction.on_commit(lambda: emails.send_kyc_approved_email(user, locked))

    submission.refresh_from_db()


def reject_submission(submission, admin_user, reason: str) -> None:
    """Idempotent. Pending -> rejected; user stays Tier 1 and may resubmit."""
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("rejection_reason_required", "A rejection reason is required.", status=400)

    User = get_user_model()
    with transaction.atomic():
        locked = KYCSubmission.objects.select_for_update().get(pk=submission.pk)
        if locked.status != KYCStatus.PENDING:
            return
        user = User.objects.select_for_update().get(pk=locked.user_id)

        locked.status = KYCStatus.REJECTED
        locked.reviewed_by = admin_user
        locked.reviewed_at = timezone.now()
        locked.rejection_reason = reason
        locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])

        user.kyc_status = KYCStatus.REJECTED  # tier intentionally untouched (stays 1)
        user.save(update_fields=["kyc_status"])

        transaction.on_commit(lambda: emails.send_kyc_rejected_email(user, locked))

    submission.refresh_from_db()
