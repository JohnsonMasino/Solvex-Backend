import hashlib

from rest_framework.throttling import SimpleRateThrottle

from .utils import get_client_ip


class AuthSensitiveThrottle(SimpleRateThrottle):
    """register, login, otp verify, reset... keyed by client IP."""
    scope = "auth_sensitive"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": get_client_ip(request)}


class OtpSendThrottle(SimpleRateThrottle):
    """OTP / reset-link sends, keyed by IP + the email in the request body."""
    scope = "otp_send"

    def get_cache_key(self, request, view):
        try:
            email = str(request.data.get("email", "")).strip().lower()
        except Exception:
            email = ""
        ident = hashlib.sha256(f"{get_client_ip(request)}|{email}".encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": ident}


class WithdrawalThrottle(SimpleRateThrottle):
    """Withdrawal requests, keyed by user id."""
    scope = "withdrawal"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = str(user.pk) if user is not None and user.is_authenticated else get_client_ip(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
