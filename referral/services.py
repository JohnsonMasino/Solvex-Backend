"""
referral/services.py

ALL referral business logic + the public cross-app contract (General Guide §8):

    get_or_create_referral_profile(user) -> ReferralProfile
    validate_code(code)                  -> User | None
    attach_referrer(new_user, code)      -> None
    on_user_verified(user)               -> None
    process_first_trade_completion(user) -> None
    build_referral_link(code)            -> str

Only wallet.services may be called from here, and ONLY through lazy imports
(see `_wallet()`), never at module level.
"""
import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.utils import money
from referral import emails
from referral.models import Referral, ReferralProfile
from referral.utils import CODE_ALPHABET, CODE_LENGTH, normalize_code

logger = logging.getLogger(__name__)

MAX_CODE_ATTEMPTS = 10

STAGE_REGISTERED = "registered"
STAGE_VERIFIED = "verified"
STAGE_FUNDED = "funded"
STAGE_REWARDED = "rewarded"
STAGES = (STAGE_REGISTERED, STAGE_VERIFIED, STAGE_FUNDED, STAGE_REWARDED)


def _wallet():
    """Lazy import: never import wallet at module level (avoids circular imports)."""
    from wallet import services as wallet_services

    return wallet_services


# --------------------------------------------------------------------------- #
# Codes & links
# --------------------------------------------------------------------------- #
def _random_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def get_or_create_referral_profile(user) -> ReferralProfile:
    """Defensive getter. Retries on code collision (up to 10 times, then raises)."""
    profile = ReferralProfile.objects.filter(user=user).first()
    if profile:
        return profile

    for _ in range(MAX_CODE_ATTEMPTS):
        try:
            with transaction.atomic():
                return ReferralProfile.objects.create(user=user, code=_random_code())
        except IntegrityError:
            # Either the code collided or another request created the profile first.
            existing = ReferralProfile.objects.filter(user=user).first()
            if existing:
                return existing
    raise RuntimeError("Could not generate a unique referral code after 10 attempts.")


def build_referral_link(code) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    path = getattr(settings, "FRONTEND_SIGNUP_PATH", "/signup")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}?ref={code}"


def validate_code(code):
    """Case-insensitive, stripped lookup. Returns the owner (User) or None."""
    code = normalize_code(code)
    if not code:
        return None
    profile = ReferralProfile.objects.select_related("user").filter(code=code).first()
    if profile is None or not profile.user.is_active:
        return None
    return profile.user


# --------------------------------------------------------------------------- #
# Tracking
# --------------------------------------------------------------------------- #
def attach_referrer(new_user, code: str) -> None:
    """
    Create a pending Referral for `new_user`. No-op (never raises) when the code is
    invalid, the owner is inactive, it is a self-referral, or the user already has one.
    """
    referrer = validate_code(code)
    if referrer is None:
        return
    if referrer.pk == new_user.pk:
        return
    if Referral.objects.filter(referred=new_user).exists():
        return
    try:
        with transaction.atomic():
            Referral.objects.create(
                referrer=referrer,
                referred=new_user,
                code_used=normalize_code(code),
                status=Referral.Status.PENDING,
            )
    except IntegrityError:
        logger.info("referral: attach_referrer ignored (duplicate/constraint) for user %s", new_user.pk)


def on_user_verified(user) -> None:
    """Referred user verified their email: stamp verified_at once and notify the referrer."""
    try:
        with transaction.atomic():
            referral = (
                Referral.objects.select_for_update(of=("self",))
                .select_related("referrer", "referred")
                .filter(referred=user, status=Referral.Status.PENDING)
                .first()
            )
            if referral is None or referral.verified_at is not None:
                return
            referral.verified_at = timezone.now()
            referral.save(update_fields=["verified_at", "updated_at"])
            referrer, referred = referral.referrer, referral.referred
            transaction.on_commit(lambda: emails.send_referral_signup_email(referrer, referred))
    except Exception:  # never raises into the authentication flow
        logger.exception("referral: on_user_verified failed for user %s", getattr(user, "pk", None))


def process_first_trade_completion(user) -> None:
    """
    Called by the trade app after every trade closes. Pays the referrer once, when the
    referred user is verified, has a completed deposit, and the referrer is active.
    Idempotent; if a condition is missing it returns and is retried on the next closed trade.
    """
    wallet = _wallet()
    with transaction.atomic():
        referral = (
            Referral.objects.select_for_update(of=("self",))
            .select_related("referrer", "referred")
            .filter(referred=user)
            .first()
        )
        if referral is None:
            return
        if referral.status != Referral.Status.PENDING:
            return  # already rewarded -> idempotent

        referrer, referred = referral.referrer, referral.referred
        if not referred.is_email_verified or not referrer.is_active:
            return
        if not wallet.has_completed_deposit(referred):
            return

        amount = money(settings.REFERRAL_BONUS_USD)
        reference = f"referral:{referral.id}"
        wallet.credit_wallet(
            referrer,
            amount,
            "referral_bonus",
            reference=reference,
            description="Referral bonus",
        )

        referral.status = Referral.Status.REWARDED
        referral.rewarded_at = timezone.now()
        referral.bonus_amount = amount
        referral.wallet_reference = reference
        referral.save(
            update_fields=["status", "rewarded_at", "bonus_amount", "wallet_reference", "updated_at"]
        )
        transaction.on_commit(lambda: emails.send_referral_bonus_email(referrer, referral))


# --------------------------------------------------------------------------- #
# Read models for the dashboard endpoints
# --------------------------------------------------------------------------- #
def get_stage(referral) -> str:
    """registered -> verified -> funded -> rewarded (computed live, cached per instance)."""
    cached = getattr(referral, "_stage", None)
    if cached:
        return cached
    if referral.status == Referral.Status.REWARDED:
        stage = STAGE_REWARDED
    elif not referral.referred.is_email_verified:
        stage = STAGE_REGISTERED
    elif _wallet().has_completed_deposit(referral.referred):
        stage = STAGE_FUNDED
    else:
        stage = STAGE_VERIFIED
    referral._stage = stage
    return stage


def _user_referrals(user):
    return Referral.objects.filter(referrer=user).select_related("referred").order_by("-created_at")


def list_referrals(user, stage=None):
    """Queryset (no stage filter) or list (stage filter, since stage depends on the wallet)."""
    qs = _user_referrals(user)
    if not stage:
        return qs
    return [r for r in qs if get_stage(r) == stage]


def list_earnings(user):
    return (
        Referral.objects.filter(referrer=user, status=Referral.Status.REWARDED)
        .select_related("referred")
        .order_by("-rewarded_at")
    )


def total_earned(user) -> Decimal:
    total = (
        Referral.objects.filter(referrer=user, status=Referral.Status.REWARDED)
        .aggregate(total=Sum("bonus_amount"))["total"]
    )
    return money(total or 0)


def get_dashboard(user) -> dict:
    profile = get_or_create_referral_profile(user)
    bonus = money(settings.REFERRAL_BONUS_USD)
    referrals = list(_user_referrals(user))
    stages = [get_stage(r) for r in referrals]

    pending = sum(1 for r in referrals if r.status == Referral.Status.PENDING)
    stats = {
        "total_referred": len(referrals),
        # cumulative funnel counts
        "verified": sum(1 for s in stages if s != STAGE_REGISTERED),
        "funded": sum(1 for s in stages if s in (STAGE_FUNDED, STAGE_REWARDED)),
        "rewarded": sum(1 for s in stages if s == STAGE_REWARDED),
        "pending": pending,
        "total_earned": total_earned(user),
        "potential_earnings": money(bonus * pending),
    }
    return {
        "code": profile.code,
        "link": build_referral_link(profile.code),
        "bonus_per_referral": bonus,
        "currency": "USD",
        "stats": stats,
        "recent": referrals[:5],
    }
