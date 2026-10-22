import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission

from .exceptions import BusinessError


class IsEmailVerified(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if not user.is_email_verified:
            raise BusinessError(
                "email_not_verified", "Please verify your email address to continue.", status=403
            )
        return True


class IsKYCApproved(BasePermission):
    message = "Complete identity verification (KYC) and wait for approval before using this feature."
    code = "kyc_required"

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.kyc_status != "approved":
            raise BusinessError(
                "kyc_required", self.message, status=403, data={"kyc_status": user.kyc_status}
            )
        return True


class HasCronSecret(BasePermission):
    def has_permission(self, request, view):
        secret = getattr(settings, "CRON_SECRET_KEY", "") or ""
        provided = request.headers.get("X-Cron-Secret", "") or ""
        if not secret or not provided or not hmac.compare_digest(provided.encode(), secret.encode()):
            raise BusinessError("invalid_cron_secret", "Invalid or missing cron secret.", status=403)
        return True
