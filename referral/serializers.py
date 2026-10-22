import re

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.utils import mask_email
from referral import services
from referral.utils import mask_name, normalize_code

STAGE_CHOICES = list(services.STAGES)


# ---------------------------------------------------------------- query params
class ReferralListQuerySerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=STAGE_CHOICES, required=False)


class ValidateCodeQuerySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=64)

    def validate_code(self, value):
        value = normalize_code(value)
        if not re.fullmatch(r"[A-Z0-9]{4,12}", value):
            raise serializers.ValidationError("Enter a valid referral code (4-12 letters or numbers).")
        return value


# ------------------------------------------------------------------ items
class RecentReferralSerializer(serializers.Serializer):
    """Privacy-safe view of a referred user (never full name / email)."""

    masked_name = serializers.SerializerMethodField()
    masked_email = serializers.SerializerMethodField()
    stage = serializers.SerializerMethodField()
    joined_at = serializers.DateTimeField(source="referred.date_joined", read_only=True)
    rewarded_at = serializers.DateTimeField(read_only=True, allow_null=True)
    bonus_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True, allow_null=True)

    @extend_schema_field(serializers.CharField())
    def get_masked_name(self, obj):
        return mask_name(obj.referred.full_name)

    @extend_schema_field(serializers.CharField())
    def get_masked_email(self, obj):
        return mask_email(obj.referred.email)

    @extend_schema_field(serializers.ChoiceField(choices=STAGE_CHOICES))
    def get_stage(self, obj):
        return services.get_stage(obj)


class ReferralItemSerializer(RecentReferralSerializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    verified_at = serializers.DateTimeField(read_only=True, allow_null=True)


class EarningItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    masked_name = serializers.SerializerMethodField()
    bonus_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True, allow_null=True)
    rewarded_at = serializers.DateTimeField(read_only=True, allow_null=True)

    @extend_schema_field(serializers.CharField())
    def get_masked_name(self, obj):
        return mask_name(obj.referred.full_name)


# -------------------------------------------------------------- dashboard
class ReferralStatsSerializer(serializers.Serializer):
    total_referred = serializers.IntegerField()
    verified = serializers.IntegerField()
    funded = serializers.IntegerField()
    rewarded = serializers.IntegerField()
    pending = serializers.IntegerField()
    total_earned = serializers.DecimalField(max_digits=18, decimal_places=2)
    potential_earnings = serializers.DecimalField(max_digits=18, decimal_places=2)


class ReferralDashboardSerializer(serializers.Serializer):
    code = serializers.CharField()
    link = serializers.CharField()
    bonus_per_referral = serializers.DecimalField(max_digits=18, decimal_places=2)
    currency = serializers.CharField()
    stats = ReferralStatsSerializer()
    recent = RecentReferralSerializer(many=True)


# ---------------------------------------------- documentation-only wrappers
class ReferralListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ReferralItemSerializer(many=True)


class EarningsDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_earned = serializers.DecimalField(max_digits=18, decimal_places=2)
    results = EarningItemSerializer(many=True)


class ValidateCodeDataSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    referrer_name = serializers.CharField()
