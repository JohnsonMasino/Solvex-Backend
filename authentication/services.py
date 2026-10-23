"""
authentication/services.py
All business logic for the authentication app. Views stay thin.
Nothing here is consumed by other apps (they only use the User model).
"""

import hmac
import logging
import math
import secrets
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.exceptions import BusinessError
from core.utils import generate_otp, hash_value, verify_hash

from . import emails
from .models import EmailOTP, PasswordResetToken, User
from .validators import password_error_messages

logger = logging.getLogger(__name__)

REGISTRATION = EmailOTP.Purpose.REGISTRATION
VALIDATION_MESSAGE = "Please correct the highlighted fields."


# --------------------------------------------------------------------------- helpers
def _referral():
    """Optional integration: referral is built after authentication."""
    if apps.is_installed("referral"):
        from referral import services
        return services
    return None


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _otp_digest(user, code: str) -> str:
    return hash_value(f"{user.pk}:{code}")


def _cooldown_remaining(user) -> int:
    """Seconds left before another OTP may be sent (0 = allowed)."""
    last = (
        EmailOTP.objects.filter(user=user, purpose=REGISTRATION)
        .order_by("-last_sent_at")
        .first()
    )
    if not last:
        return 0
    elapsed = (timezone.now() - last.last_sent_at).total_seconds()
    remaining = settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed
    return max(0, math.ceil(remaining))


def _reset_login_counters(user_id):
    User.objects.filter(pk=user_id).update(
        failed_login_attempts=0, locked_until=None, updated_at=timezone.now()
    )


# --------------------------------------------------------------------------- OTP
def issue_otp(user, *, enforce_cooldown: bool = True) -> bool:
    """
    Create a fresh registration OTP (older unused ones are invalidated) and email it.
    Returns True if the email was handed to Mailgun successfully.
    Raises BusinessError(otp_cooldown, 429) when called too soon.
    NOTE: call this OUTSIDE an open transaction.atomic() block so that the
    on_commit callback runs immediately and `otp_sent` is accurate.
    """
    if enforce_cooldown:
        remaining = _cooldown_remaining(user)
        if remaining > 0:
            raise BusinessError(
                "otp_cooldown",
                f"Please wait {remaining} seconds before requesting a new code.",
                status=429,
                data={"retry_after_seconds": remaining},
            )

    code = generate_otp()
    now = timezone.now()
    with transaction.atomic():
        EmailOTP.objects.filter(user=user, purpose=REGISTRATION, is_used=False).update(
            is_used=True, updated_at=now
        )
        EmailOTP.objects.create(
            user=user,
            purpose=REGISTRATION,
            code_hash=_otp_digest(user, code),
            expires_at=now + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
            last_sent_at=now,
        )

    result = {"sent": False}

    def _send():
        result["sent"] = bool(emails.send_registration_otp_email(user, code))

    transaction.on_commit(_send)
    return result["sent"]


def verify_otp(email: str, otp: str) -> User:
    email = _norm(email)
    user = User.objects.filter(email=email).first()
    if user is None:
        raise BusinessError("user_not_found", "No pending verification for this email.", status=404)
    if user.is_email_verified:
        raise BusinessError("already_verified", "This email address is already verified.")

    max_attempts = settings.OTP_MAX_ATTEMPTS
    outcome, remaining = None, 0

    # The outcome is decided inside the lock but raised AFTER the block,
    # otherwise the attempt counter increment would be rolled back.
    with transaction.atomic():
        now = timezone.now()
        record = (
            EmailOTP.objects.select_for_update()
            .filter(user=user, purpose=REGISTRATION, is_used=False)
            .order_by("-created_at")
            .first()
        )
        if record is None or record.expires_at <= now:
            outcome = "otp_expired"
        elif record.attempts >= max_attempts:
            outcome = "otp_attempts_exceeded"
        elif not hmac.compare_digest(_otp_digest(user, otp), record.code_hash):
            record.attempts += 1
            record.save(update_fields=["attempts", "updated_at"])
            if record.attempts >= max_attempts:
                outcome = "otp_attempts_exceeded"
            else:
                outcome, remaining = "invalid_otp", max_attempts - record.attempts
        else:
            record.is_used = True
            record.save(update_fields=["is_used", "updated_at"])
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            locked_user.is_email_verified = True
            locked_user.save(update_fields=["is_email_verified", "updated_at"])
            user = locked_user
            outcome = "ok"
            transaction.on_commit(lambda: _after_verified(user))

    if outcome == "ok":
        return user
    if outcome == "invalid_otp":
        raise BusinessError(
            "invalid_otp", "The code you entered is incorrect.", data={"attempts_remaining": remaining}
        )
    if outcome == "otp_attempts_exceeded":
        raise BusinessError(
            "otp_attempts_exceeded", "Too many incorrect attempts. Please request a new code."
        )
    raise BusinessError("otp_expired", "This code has expired. Please request a new one.")


def _after_verified(user):
    emails.send_welcome_email(user)
    referral = _referral()
    if referral is not None:
        try:
            referral.on_user_verified(user)
        except Exception:  # never break verification because of referral
            logger.exception("referral.on_user_verified failed for user %s", user.pk)


def resend_otp(email: str) -> None:
    """Silent for unknown emails (no account enumeration)."""
    user = User.objects.filter(email=_norm(email)).first()
    if user is None or not user.is_active:
        return
    if user.is_email_verified:
        raise BusinessError("already_verified", "This email address is already verified.")
    issue_otp(user)  # may raise otp_cooldown


# --------------------------------------------------------------------------- register
def register_user(*, full_name, email, password, referral_code=None):
    """Returns (user, otp_sent)."""
    email = _norm(email)
    code = (referral_code or "").strip().upper()

    referral = None
    if code:
        referral = _referral()
        if referral is None:
            logger.warning("Referral code supplied but the referral app is not installed; ignoring it.")
        elif referral.validate_code(code) is None:
            raise BusinessError("invalid_referral_code", "That referral code is invalid.")

    try:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(email=email).first()
            if user is not None:
                if user.is_email_verified:
                    raise BusinessError(
                        "email_already_registered", "An account with this email already exists."
                    )
                # Unverified account = abandoned OTP step -> treat as a retry.
                user.full_name = full_name
                user.set_password(password)
                user.save(update_fields=["full_name", "password", "updated_at"])
            else:
                user = User.objects.create_user(email=email, password=password, full_name=full_name)
    except IntegrityError:
        raise BusinessError("email_already_registered", "An account with this email already exists.")

    if referral is not None and code:
        try:
            referral.attach_referrer(user, code)
        except Exception:
            logger.exception("referral.attach_referrer failed for user %s", user.pk)

    if _cooldown_remaining(user) > 0:
        otp_sent = True  # a valid code was sent moments ago
    else:
        otp_sent = issue_otp(user, enforce_cooldown=False)
    return user, otp_sent


# --------------------------------------------------------------------------- login
def _locked_error(locked_until):
    seconds = max(1, math.ceil((locked_until - timezone.now()).total_seconds()))
    minutes = math.ceil(seconds / 60)
    return BusinessError(
        "account_locked",
        f"Too many failed login attempts. Try again in {minutes} minute(s).",
        status=429,
        data={"retry_after_seconds": seconds},
    )


def _register_failed_login(user):
    """Increment the counter; returns locked_until if this failure locked the account."""
    locked_until = None
    with transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        u.failed_login_attempts += 1
        if u.failed_login_attempts >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            u.locked_until = locked_until
            u.failed_login_attempts = 0
        u.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])
    return locked_until


def authenticate_user(email: str, password: str, request=None):
    """Returns (user, {"access", "refresh"}) or raises BusinessError."""
    email = _norm(email)
    invalid = BusinessError("invalid_credentials", "Invalid email or password.")

    user = User.objects.filter(email=email).first()
    if user is None:
        make_password(password)  # equalise timing, avoid revealing unknown emails
        raise invalid

    # 1. lockout check BEFORE verifying the password
    if user.locked_until and user.locked_until > timezone.now():
        raise _locked_error(user.locked_until)

    # 2. password
    if not user.check_password(password):
        locked_until = _register_failed_login(user)
        if locked_until:
            raise _locked_error(locked_until)
        raise invalid

    _reset_login_counters(user.pk)

    # 3. state gates (only revealed after correct credentials)
    if not user.is_active:
        raise BusinessError("account_disabled", "This account has been disabled. Please contact support.", status=403)

    if not user.is_email_verified:
        otp_sent = False
        if _cooldown_remaining(user) == 0:
            otp_sent = issue_otp(user, enforce_cooldown=False)
        raise BusinessError(
            "email_not_verified",
            "Please verify your email address before logging in.",
            status=403,
            data={"email": user.email, "otp_sent": otp_sent},
        )

    # 4. success
    now = timezone.now()
    User.objects.filter(pk=user.pk).update(last_login=now)
    user.last_login = now
    refresh = RefreshToken.for_user(user)
    return user, {"access": str(refresh.access_token), "refresh": str(refresh)}


# --------------------------------------------------------------------------- tokens
def blacklist_all_tokens(user, exclude_jti: str = None) -> int:
    """Blacklist every outstanding, not-yet-blacklisted refresh token of the user."""
    qs = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    if exclude_jti:
        qs = qs.exclude(jti=exclude_jti)
    rows = [BlacklistedToken(token=t) for t in qs]
    BlacklistedToken.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def logout_user(user, raw_refresh: str) -> None:
    bad = BusinessError(
        "invalid_refresh_token", "The refresh token is invalid, expired or already logged out."
    )
    try:
        token = RefreshToken(raw_refresh)  # verify() also rejects blacklisted tokens
    except TokenError:
        raise bad
    if str(token.payload.get(jwt_settings.USER_ID_CLAIM)) != str(user.pk):
        raise bad
    try:
        token.blacklist()
    except TokenError:
        raise bad


# --------------------------------------------------------------------------- password reset
def _build_reset_url(user, token: str) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}{settings.FRONTEND_RESET_PASSWORD_PATH}/{uid}/{token}"


def create_reset_token(user) -> str:
    """Creates a single-use token (older unused ones are removed). Returns the RAW token."""
    raw = secrets.token_urlsafe(32)
    with transaction.atomic():
        PasswordResetToken.objects.filter(user=user, used_at__isnull=True).delete()
        PasswordResetToken.objects.create(
            user=user,
            token_hash=hash_value(raw),
            expires_at=timezone.now() + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRY_MINUTES),
        )
    return raw


def request_password_reset(email: str) -> None:
    """Never reveals whether the account exists. Only verified + active users get an email."""
    user = User.objects.filter(email=_norm(email)).first()
    if user is None or not user.is_active or not user.is_email_verified:
        return
    raw = create_reset_token(user)
    url = _build_reset_url(user, raw)
    transaction.on_commit(lambda: emails.send_password_reset_email(user, url))


def _get_reset_record(uid: str, token: str):
    invalid = BusinessError("invalid_reset_link", "This password reset link is invalid.")
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)))
    except (TypeError, ValueError, OverflowError, UnicodeDecodeError, DjangoValidationError, User.DoesNotExist):
        raise invalid
    if not user.is_active:
        raise invalid
    record = PasswordResetToken.objects.filter(user=user, token_hash=hash_value(token)).first()
    if record is None:
        raise invalid
    if record.used_at is not None:
        raise BusinessError("reset_link_used", "This password reset link has already been used.")
    if record.expires_at <= timezone.now():
        raise BusinessError("reset_link_expired", "This password reset link has expired. Please request a new one.")
    return user, record


def validate_reset_link(uid: str, token: str) -> bool:
    _get_reset_record(uid, token)
    return True


def reset_password(uid: str, token: str, new_password: str) -> None:
    user, record = _get_reset_record(uid, token)
    with transaction.atomic():
        record = PasswordResetToken.objects.select_for_update().get(pk=record.pk)
        if record.used_at is not None:
            raise BusinessError("reset_link_used", "This password reset link has already been used.")
        if record.expires_at <= timezone.now():
            raise BusinessError("reset_link_expired", "This password reset link has expired. Please request a new one.")

        user = User.objects.select_for_update().get(pk=user.pk)
        if user.check_password(new_password):
            raise BusinessError("password_reuse", "Your new password must be different from your current password.")
        problems = password_error_messages(new_password, user)
        if problems:
            raise BusinessError(
                "validation_error", VALIDATION_MESSAGE, errors={"new_password": problems}
            )

        user.set_password(new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["password", "failed_login_attempts", "locked_until", "updated_at"])
        record.used_at = timezone.now()
        record.save(update_fields=["used_at", "updated_at"])
        PasswordResetToken.objects.filter(user=user, used_at__isnull=True).delete()
        blacklist_all_tokens(user)
        transaction.on_commit(lambda: emails.send_password_changed_email(user))


def change_password(user, current_password: str, new_password: str, keep_refresh: str = None) -> None:
    """
    Logged-in password change. All other refresh tokens are blacklisted.
    If the client passes its current `refresh` token, that one session is kept.
    """
    if not user.check_password(current_password):
        raise BusinessError("invalid_current_password", "Your current password is incorrect.")
    if current_password == new_password or user.check_password(new_password):
        raise BusinessError("password_reuse", "Your new password must be different from your current password.")
    problems = password_error_messages(new_password, user)
    if problems:
        raise BusinessError("validation_error", VALIDATION_MESSAGE, errors={"new_password": problems})

    keep_jti = None
    if keep_refresh:
        try:
            token = RefreshToken(keep_refresh)
            if str(token.payload.get(jwt_settings.USER_ID_CLAIM)) == str(user.pk):
                keep_jti = token.payload.get("jti")
        except TokenError:
            pass

    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        locked.set_password(new_password)
        locked.save(update_fields=["password", "updated_at"])
        blacklist_all_tokens(locked, exclude_jti=keep_jti)
        transaction.on_commit(lambda: emails.send_password_changed_email(locked))


# --------------------------------------------------------------------------- cron
def _deleted(qs, label: str) -> int:
    return qs.delete()[1].get(label, 0)


def cleanup_expired_records() -> dict:
    now = timezone.now()
    cutoff = now - timedelta(days=7)
    from django.db.models import Q

    otps = _deleted(
        EmailOTP.objects.filter(Q(expires_at__lt=cutoff) | Q(is_used=True, updated_at__lt=cutoff)),
        "authentication.EmailOTP",
    )
    resets = _deleted(
        PasswordResetToken.objects.filter(Q(expires_at__lt=cutoff) | Q(used_at__lt=cutoff)),
        "authentication.PasswordResetToken",
    )
    outstanding = _deleted(
        OutstandingToken.objects.filter(expires_at__lte=now),  # cascades to BlacklistedToken
        "token_blacklist.OutstandingToken",
    )
    return {
        "deleted_otps": otps,
        "deleted_reset_tokens": resets,
        "deleted_outstanding_tokens": outstanding,
    }
