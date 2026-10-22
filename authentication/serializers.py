import re

from django.conf import settings
from rest_framework import serializers

from .models import User
from .validators import password_error_messages

MISMATCH = "Passwords do not match."


class LowerEmailField(serializers.EmailField):
    """Email that is always stripped + lowercased."""

    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 254)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().lower()


def _password_field(**kwargs):
    return serializers.CharField(write_only=True, trim_whitespace=False, style={"input_type": "password"}, **kwargs)


def _check_strength(value, user=None):
    problems = password_error_messages(value, user)
    if problems:
        raise serializers.ValidationError(problems)
    return value


# --------------------------------------------------------------------------- register / otp
class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    email = LowerEmailField()
    password = _password_field(max_length=128)
    confirm_password = _password_field(max_length=128)
    referral_code = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=32)

    def validate_full_name(self, value):
        value = re.sub(r"\s+", " ", value).strip()
        if not 2 <= len(value) <= 150:
            raise serializers.ValidationError("Full name must be between 2 and 150 characters.")
        if not any(c.isalpha() for c in value) or any(not (c.isalpha() or c in " -'.\u2019") for c in value):
            raise serializers.ValidationError(
                "Full name may only contain letters, spaces, hyphens, apostrophes and dots."
            )
        return value

    def validate_password(self, value):
        return _check_strength(value)

    def validate_referral_code(self, value):
        return (value or "").strip().upper()

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": [MISMATCH]})
        attrs.pop("confirm_password")
        return attrs


class VerifyOTPSerializer(serializers.Serializer):
    email = LowerEmailField()
    otp = serializers.CharField(max_length=16)

    def validate_otp(self, value):
        value = value.strip()
        if not value.isdigit() or len(value) != settings.OTP_LENGTH:
            raise serializers.ValidationError(f"Enter the {settings.OTP_LENGTH}-digit code.")
        return value


class ResendOTPSerializer(serializers.Serializer):
    email = LowerEmailField()


# --------------------------------------------------------------------------- login / tokens
class LoginSerializer(serializers.Serializer):
    email = LowerEmailField()
    password = _password_field(max_length=128)


class RefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class SessionUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "full_name", "is_email_verified", "tier", "kyc_status")
        read_only_fields = fields


class MeSerializer(SessionUserSerializer):
    class Meta(SessionUserSerializer.Meta):
        fields = SessionUserSerializer.Meta.fields + ("date_joined",)
        read_only_fields = fields


# --------------------------------------------------------------------------- passwords
class PasswordResetRequestSerializer(serializers.Serializer):
    email = LowerEmailField()


class PasswordResetValidateSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=128)
    token = serializers.CharField(max_length=256)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=128)
    token = serializers.CharField(max_length=256)
    new_password = _password_field(max_length=128)
    confirm_password = _password_field(max_length=128)

    def validate_new_password(self, value):
        return _check_strength(value)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": [MISMATCH]})
        attrs.pop("confirm_password")
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = _password_field(max_length=128)
    new_password = _password_field(max_length=128)
    confirm_password = _password_field(max_length=128)
    refresh = serializers.CharField(
        required=False, allow_blank=True, write_only=True,
        help_text="Optional. Your current refresh token; if sent, this session stays logged in.",
    )

    def validate_new_password(self, value):
        return _check_strength(value, user=getattr(self.context.get("request"), "user", None))

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": [MISMATCH]})
        attrs.pop("confirm_password")
        return attrs
