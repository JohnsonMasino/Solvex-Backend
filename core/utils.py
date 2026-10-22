import hmac
import secrets
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

_TWO_DP = Decimal("0.01")
_SIX_DP = Decimal("0.000001")


def money(value) -> Decimal:
    """USD, 2 dp, ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)


def usdt(value) -> Decimal:
    """USDT, 6 dp, ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(_SIX_DP, rounding=ROUND_HALF_UP)


def generate_otp(length: int = None) -> str:
    length = length or settings.OTP_LENGTH
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_value(raw) -> str:
    """HMAC-SHA256 keyed with SECRET_KEY. Used for OTPs and reset tokens (never store plain)."""
    return hmac.new(settings.SECRET_KEY.encode(), str(raw).encode(), sha256).hexdigest()


def verify_hash(raw, hashed: str) -> bool:
    return hmac.compare_digest(hash_value(raw), hashed or "")


def get_client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def mask_email(email: str) -> str:
    """john@gmail.com -> j***@gmail.com"""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_tail(value: str, keep: int = 4) -> str:
    """123456789 -> *****6789 style: ****6789"""
    value = str(value or "")
    return "****" + value[-keep:] if value else ""


def is_weekend(dt: datetime = None) -> bool:
    """Saturday/Sunday in TRADING_TIMEZONE."""
    dt = dt or timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, ZoneInfo("UTC"))
    local = dt.astimezone(ZoneInfo(getattr(settings, "TRADING_TIMEZONE", "UTC")))
    return local.weekday() >= 5
