from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from core.openapi import error_response_doc, success_response_doc
from core.permissions import HasCronSecret
from core.responses import success_response
from core.throttles import AuthSensitiveThrottle, OtpSendThrottle

from . import services
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PasswordResetValidateSerializer,
    RefreshRequestSerializer,
    RegisterSerializer,
    ResendOTPSerializer,
    SessionUserSerializer,
    VerifyOTPSerializer,
)

TAG = "Authentication"
USER_EXAMPLE = {
    "id": "3f2b8c1e-5a47-4c1d-9e0a-7d2b6c9f1a10",
    "email": "john@example.com",
    "full_name": "John Doe",
    "is_email_verified": True,
    "tier": 1,
    "kyc_status": "not_submitted",
}


def _validation(errors=None, suffix=""):
    return error_response_doc(
        "Validation error.", code="validation_error",
        message="Please correct the highlighted fields.",
        errors=errors or {"field_name": ["This field is required."]},
    )


def _throttled():
    return error_response_doc("Too many requests.", code="throttled",
                              message="Request was throttled. Try again later.")


def _unauth():
    return error_response_doc("Not authenticated.", code="not_authenticated",
                              message="Authentication credentials were not provided.")


GENERIC_RESET_MESSAGE = "If an account with that email exists, a password reset link has been sent."


# =========================================================================== register
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Register", auth=[],
        description="Creates the account and emails a 6-digit verification code. "
                    "Re-registering an unverified email resends a fresh code.",
        request=RegisterSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={
            "full_name": "John Doe", "email": "john@example.com", "password": "StrongPass123",
            "confirm_password": "StrongPass123", "referral_code": "A8K2QZ7M"})],
        responses={
            201: success_response_doc("RegisterSuccess", "Account created, OTP sent.",
                message="Account created. We've sent a 6-digit verification code to your email.",
                example={"success": True,
                         "message": "Account created. We've sent a 6-digit verification code to your email.",
                         "data": {"email": "john@example.com", "otp_sent": True, "otp_expires_in_minutes": 10}}),
            400: _validation({"confirm_password": ["Passwords do not match."]}),
            429: _throttled(),
        },
    )
    def post(self, request):
        from django.conf import settings

        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user, otp_sent = services.register_user(**s.validated_data)
        message = (
            "Account created. We've sent a 6-digit verification code to your email."
            if otp_sent else
            "Account created, but we couldn't send the verification email. Please request a new code."
        )
        return success_response(
            {"email": user.email, "otp_sent": otp_sent, "otp_expires_in_minutes": settings.OTP_EXPIRY_MINUTES},
            message=message, status=201,
        )


class VerifyOTPView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Verify email OTP", auth=[],
        description="Verifies the emailed code. The user is NOT logged in automatically.",
        request=VerifyOTPSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={"email": "john@example.com", "otp": "123456"})],
        responses={
            200: success_response_doc("VerifyOtpSuccess", "Email verified.",
                message="Email verified successfully. You can now log in.",
                example={"success": True, "message": "Email verified successfully. You can now log in.",
                         "data": {"email": "john@example.com", "is_email_verified": True}}),
            400: error_response_doc("Wrong code, expired, attempts exceeded or already verified.",
                                    code="invalid_otp", message="The code you entered is incorrect."),
            404: error_response_doc("No such account.", code="user_not_found",
                                    message="No pending verification for this email."),
            429: _throttled(),
        },
    )
    def post(self, request):
        s = VerifyOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = services.verify_otp(s.validated_data["email"], s.validated_data["otp"])
        return success_response(
            {"email": user.email, "is_email_verified": True},
            message="Email verified successfully. You can now log in.",
        )


class ResendOTPView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpSendThrottle]

    @extend_schema(
        tags=[TAG], summary="Resend OTP", auth=[],
        description="Sends a new verification code. Always returns the same response for unknown emails.",
        request=ResendOTPSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={"email": "john@example.com"})],
        responses={
            200: success_response_doc("ResendOtpSuccess", "Generic confirmation.",
                message="If an unverified account exists, a new code has been sent.",
                example={"success": True, "message": "If an unverified account exists, a new code has been sent.", "data": {}}),
            400: error_response_doc("Already verified.", code="already_verified",
                                    message="This email address is already verified."),
            429: error_response_doc("Within cooldown.", code="otp_cooldown",
                                    message="Please wait 42 seconds before requesting a new code."),
        },
    )
    def post(self, request):
        s = ResendOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.resend_otp(s.validated_data["email"])
        return success_response(message="If an unverified account exists, a new code has been sent.")


# =========================================================================== login / tokens
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Login", auth=[],
        description="Authenticates with email and password. Returns JWT access and refresh tokens.",
        request=LoginSerializer,
        examples=[OpenApiExample("Valid request", request_only=True,
                                 value={"email": "user@example.com", "password": "StrongPass123"})],
        responses={
            200: success_response_doc("LoginSuccess", "Login successful. Tokens returned.",
                message="Login successful.",
                example={"success": True, "message": "Login successful.",
                         "data": {"access": "<jwt>", "refresh": "<jwt>", "user": USER_EXAMPLE}}),
            400: error_response_doc("Invalid credentials.", code="invalid_credentials",
                                    message="Invalid email or password."),
            403: error_response_doc("Email not verified or account disabled.", code="email_not_verified",
                                    message="Please verify your email address before logging in."),
            429: error_response_doc("Account locked or too many attempts.", code="account_locked",
                                    message="Too many failed login attempts. Try again in 15 minute(s)."),
        },
    )
    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user, tokens = services.authenticate_user(
            s.validated_data["email"], s.validated_data["password"], request
        )
        return success_response(
            {**tokens, "user": SessionUserSerializer(user).data}, message="Login successful."
        )


class TokenRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Refresh access token", auth=[],
        description="Exchanges a valid refresh token for a new access token (refresh token is rotated).",
        request=RefreshRequestSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={"refresh": "<jwt>"})],
        responses={
            200: success_response_doc("TokenRefreshSuccess", "New tokens issued.",
                example={"success": True, "message": "Success", "data": {"access": "<jwt>", "refresh": "<jwt>"}}),
            400: _validation({"refresh": ["This field is required."]}),
            401: error_response_doc("Invalid, expired or blacklisted token.", code="token_not_valid",
                                    message="Token is invalid or expired."),
        },
    )
    def post(self, request):
        s = TokenRefreshSerializer(data=request.data)
        try:
            s.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])
        data = s.validated_data
        return success_response({"access": data["access"], "refresh": data.get("refresh")})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Logout",
        description="Blacklists the given refresh token. Logging out twice returns 400.",
        request=LogoutSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={"refresh": "<jwt>"})],
        responses={
            200: success_response_doc("LogoutSuccess", "Logged out.", message="Logged out successfully.",
                example={"success": True, "message": "Logged out successfully.", "data": {}}),
            400: error_response_doc("Missing or invalid refresh token.", code="invalid_refresh_token",
                                    message="The refresh token is invalid, expired or already logged out."),
            401: _unauth(),
        },
    )
    def post(self, request):
        s = LogoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.logout_user(request.user, s.validated_data["refresh"])
        return success_response(message="Logged out successfully.")


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Current user",
        description="Returns the authenticated user. Used by the frontend to restore state after a refresh.",
        responses={
            200: success_response_doc("MeSuccess", "Current user.",
                example={"success": True, "message": "Success",
                         "data": {**USER_EXAMPLE, "date_joined": "2026-10-22T14:30:00Z"}}),
            401: _unauth(),
        },
    )
    def get(self, request):
        return success_response(MeSerializer(request.user).data)


# =========================================================================== passwords
class PasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpSendThrottle]

    @extend_schema(
        tags=[TAG], summary="Request password reset link", auth=[],
        description="Emails a reset link if a verified account exists. The response never reveals whether it does.",
        request=PasswordResetRequestSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={"email": "john@example.com"})],
        responses={
            200: success_response_doc("PasswordResetRequestSuccess", "Generic confirmation.",
                message=GENERIC_RESET_MESSAGE,
                example={"success": True, "message": GENERIC_RESET_MESSAGE, "data": {}}),
            400: _validation({"email": ["Enter a valid email address."]}),
            429: _throttled(),
        },
    )
    def post(self, request):
        s = PasswordResetRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.request_password_reset(s.validated_data["email"])
        return success_response(message=GENERIC_RESET_MESSAGE)


class PasswordResetValidateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Validate reset link", auth=[],
        description="Checks a reset link before the frontend shows the new-password form.",
        request=PasswordResetValidateSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={"uid": "<uidb64>", "token": "<token>"})],
        responses={
            200: success_response_doc("PasswordResetValidateSuccess", "Link is valid.",
                example={"success": True, "message": "Success", "data": {"valid": True}}),
            400: error_response_doc("Invalid, expired or used link.", code="invalid_reset_link",
                                    message="This password reset link is invalid."),
            429: _throttled(),
        },
    )
    def post(self, request):
        s = PasswordResetValidateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.validate_reset_link(s.validated_data["uid"], s.validated_data["token"])
        return success_response({"valid": True})


class PasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Set new password", auth=[],
        description="Sets a new password using the uid and token from the emailed link. Logs out all sessions.",
        request=PasswordResetConfirmSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={
            "uid": "MQ", "token": "xxxxxxxx", "new_password": "NewStrongPass123",
            "confirm_password": "NewStrongPass123"})],
        responses={
            200: success_response_doc("PasswordResetConfirmSuccess", "Password reset.",
                message="Password reset successful. You can now log in.",
                example={"success": True, "message": "Password reset successful. You can now log in.", "data": {}}),
            400: error_response_doc("Validation, invalid/expired/used link, or password reuse.",
                                    code="reset_link_expired",
                                    message="This password reset link has expired. Please request a new one."),
            429: _throttled(),
        },
    )
    def post(self, request):
        s = PasswordResetConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        services.reset_password(d["uid"], d["token"], d["new_password"])
        return success_response(message="Password reset successful. You can now log in.")


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG], summary="Change password",
        description="Changes the password of the logged-in user and blacklists other refresh tokens.",
        request=ChangePasswordSerializer,
        examples=[OpenApiExample("Valid request", request_only=True, value={
            "current_password": "OldPass123", "new_password": "NewStrongPass123",
            "confirm_password": "NewStrongPass123", "refresh": "<optional current refresh jwt>"})],
        responses={
            200: success_response_doc("ChangePasswordSuccess", "Password changed.",
                message="Password changed successfully.",
                example={"success": True, "message": "Password changed successfully.", "data": {}}),
            400: error_response_doc("Validation, wrong current password or password reuse.",
                                    code="invalid_current_password", message="Your current password is incorrect."),
            401: _unauth(),
            429: _throttled(),
        },
    )
    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        d = s.validated_data
        services.change_password(request.user, d["current_password"], d["new_password"], d.get("refresh"))
        return success_response(message="Password changed successfully.")


# =========================================================================== cron
class CleanupCronView(APIView):
    authentication_classes = []
    permission_classes = [HasCronSecret]
    throttle_classes = []

    @extend_schema(
        tags=["Cron"], summary="Cleanup expired auth records", auth=[],
        description="Deletes expired/used OTPs and reset tokens older than 7 days and flushes expired JWT records. "
                    "Idempotent. Intended to run daily from cron-job.org.",
        parameters=[OpenApiParameter("X-Cron-Secret", str, OpenApiParameter.HEADER, required=True,
                                     description="Value of CRON_SECRET_KEY.")],
        request=None,
        responses={
            200: success_response_doc("AuthCleanupSuccess", "Cleanup summary.", message="Cleanup complete.",
                example={"success": True, "message": "Cleanup complete.",
                         "data": {"deleted_otps": 4, "deleted_reset_tokens": 2, "deleted_outstanding_tokens": 31}}),
            403: error_response_doc("Wrong or missing secret.", code="invalid_cron_secret",
                                    message="Invalid or missing cron secret."),
        },
    )
    def post(self, request):
        return success_response(services.cleanup_expired_records(), message="Cleanup complete.")
