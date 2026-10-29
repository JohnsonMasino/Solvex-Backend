"""
kyc/services.py - ALL business logic + the public cross-app contract:

    submit_kyc(user, data, request) -> KYCSubmission
    approve_submission(submission, admin_user) -> None
    reject_submission(submission, admin_user, reason) -> None
    get_avatar_url(user, size=256) -> str | None      (profile picture for the user's pages)

This app is the ONLY writer of user.tier and user.kyc_status.

Access model
------------
* Tier 1 (registered, KYC not approved): may DEPOSIT only.
* Tier 2 (KYC approved): deposit, withdraw, trade and request loans.
`get_status_summary()["access"]` exposes this so the frontend can show/hide features
from a single source of truth. The real enforcement is on the endpoints themselves
(`IsKYCApproved` on withdrawals, trades and loan requests).

Images
------
A KYC submission carries three images, uploaded through core/images.py:

* profile photo : PUBLIC  (private=False), folder  <root>/avatars
* ID front/back : PRIVATE (private=True),  folder  <root>/kyc/<user_id>

The same `private` value is used when uploading, building URLs and deleting.
Images are written once, when the submission is created. There is no replace endpoint,
so they are locked from the moment KYC is submitted. After a rejection the user
submits again with a fresh set of images (a new KYCSubmission row).
"""
import ipaddress
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import images
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

# Image delivery settings (single source of truth for this app)
PROFILE_PHOTO_PRIVATE = False
ID_IMAGE_PRIVATE = True
AVATAR_SIZE = 256
IMAGE_FORMATS = ["jpg", "png", "webp", "heic"]


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def _avatar_folder() -> str:
    return f"{settings.CLOUDINARY_FOLDER_ROOT}/avatars"


def _id_folder(user) -> str:
    return f"{settings.CLOUDINARY_FOLDER_ROOT}/kyc/{user.pk}"


def get_image_url(public_id: str, *, private: bool, **transform):
    """Builds a delivery URL, or None when there is no image / Cloudinary is unavailable."""
    if not public_id:
        return None
    try:
        return images.image_url(public_id, private=private, **transform)
    except Exception:
        logger.exception("kyc: could not build image url for %s", public_id)
        return None


def get_avatar_url(user, size: int = AVATAR_SIZE):
    """
    Profile picture of the user (square, face-centred), or None.

    Uses the newest submission that is pending or approved. A rejected submission's photo
    is not shown. Other apps (authentication / profiles) call THIS function, they must not
    read kyc models directly.
    """
    sub = (
        KYCSubmission.objects.filter(user=user, status__in=[KYCStatus.PENDING, KYCStatus.APPROVED])
        .exclude(profile_photo_public_id="")
        .order_by("-attempt_number", "-created_at")
        .first()
    )
    if sub is None:
        return None
    return get_image_url(
        sub.profile_photo_public_id, private=PROFILE_PHOTO_PRIVATE,
        width=size, height=size, crop="fill", gravity="face",
    )


def get_submission_images(sub) -> dict:
    """URLs for one submission. ID image URLs are signed and temporary: never cache them."""
    return {
        "profile_photo_url": get_image_url(
            sub.profile_photo_public_id, private=PROFILE_PHOTO_PRIVATE,
            width=AVATAR_SIZE, height=AVATAR_SIZE, crop="fill", gravity="face",
        ),
        "id_front_url": get_image_url(sub.id_front_public_id, private=ID_IMAGE_PRIVATE),
        "id_back_url": get_image_url(sub.id_back_public_id, private=ID_IMAGE_PRIVATE),
    }


def _read_image(file):
    """Validates the real file content and returns the bytes to upload."""
    try:
        file.seek(0)
    except Exception:
        pass
    return images.read_and_validate(file)


def _upload(data, folder: str, private: bool, uploaded: list) -> str:
    public_id = images.upload_image(data, folder=folder, private=private)
    uploaded.append((public_id, private))
    return public_id


def _discard(uploaded: list, saved_front_id: str = "") -> None:
    """Removes images uploaded for a submission that was NOT saved."""
    if not uploaded:
        return
    # Defensive: if the row actually committed (e.g. an on_commit hook raised), keep its images.
    if saved_front_id and KYCSubmission.objects.filter(id_front_public_id=saved_front_id).exists():
        return
    for public_id, private in uploaded:
        try:
            images.delete_image(public_id, private=private)
        except Exception:
            logger.exception("kyc: could not delete orphan image %s", public_id)


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
        "image_max_mb": settings.IMAGE_UPLOAD_MAX_MB,
        "image_formats": IMAGE_FORMATS,
    }


def get_access(user) -> dict:
    """What the user may do right now. Deposits never need KYC; everything else does."""
    approved = user.kyc_status == KYCStatus.APPROVED
    return {
        "can_deposit": True,
        "can_withdraw": approved,
        "can_trade": approved,
        "can_request_loan": approved,
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
        "access": get_access(user),
        "avatar_url": get_avatar_url(user),
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


def _attempts_error(used: int):
    return BusinessError(
        "kyc_attempts_exceeded",
        "You have reached the maximum number of verification attempts. Please contact support.",
        status=403,
        data={"attempts_used": used, "attempts_remaining": 0},
    )


def _preflight(user) -> None:
    """
    Cheap checks BEFORE any image is uploaded, so a user who can't submit anyway
    doesn't cost three Cloudinary uploads. The authoritative checks run again
    inside the locked transaction.
    """
    User = get_user_model()
    current = User.objects.filter(pk=user.pk).values_list("kyc_status", flat=True).first()
    if current == KYCStatus.PENDING:
        raise _pending_error()
    if current == KYCStatus.APPROVED:
        raise _approved_error()
    used = KYCSubmission.objects.filter(user=user).count()
    if used >= settings.KYC_MAX_ATTEMPTS:
        raise _attempts_error(used)


def _create_submission(user, data: dict, request, id_number: str, id_hash: str, image_ids: dict) -> KYCSubmission:
    User = get_user_model()
    p, a = data["personal"], data["address"]
    i, e, c = data["identification"], data["employment"], data["consent"]

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
            raise _attempts_error(used)

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
                    # images
                    profile_photo_public_id=image_ids["profile_photo"],
                    id_front_public_id=image_ids["id_front"],
                    id_back_public_id=image_ids["id_back"],
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

    return submission


def submit_kyc(user, data: dict, request) -> KYCSubmission:
    """
    `data` is the validated payload from SubmitKYCSerializer: the five nested sections plus
    the three uploaded files `profile_photo`, `id_front`, `id_back`.

    Order of work (so nothing is wasted or left behind):
      1. cheap state checks            -> no upload if the user can't submit anyway
      2. validate all three files      -> real content check, before any upload
      3. upload to Cloudinary          -> outside the DB transaction (slow network calls)
      4. locked DB transaction         -> creates the row with the three public ids
      5. on ANY failure after step 3   -> the uploaded images are deleted again
    """
    i = data["identification"]
    id_number = normalize_id_number(i["id_number"])
    id_hash = hash_value(f"{i['id_type']}:{id_number}")

    _preflight(user)

    photo = _read_image(data["profile_photo"])
    front = _read_image(data["id_front"])
    back = _read_image(data["id_back"])

    uploaded: list = []
    image_ids: dict = {}
    try:
        image_ids["profile_photo"] = _upload(photo, _avatar_folder(), PROFILE_PHOTO_PRIVATE, uploaded)
        image_ids["id_front"] = _upload(front, _id_folder(user), ID_IMAGE_PRIVATE, uploaded)
        image_ids["id_back"] = _upload(back, _id_folder(user), ID_IMAGE_PRIVATE, uploaded)

        submission = _create_submission(user, data, request, id_number, id_hash, image_ids)
    except Exception:
        _discard(uploaded, saved_front_id=image_ids.get("id_front", ""))
        raise

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