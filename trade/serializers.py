from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import DURATION_CHOICES, Trade
from .services import normalize_pair


class OpenTradeSerializer(serializers.Serializer):
    """Input for POST /api/trades/. market_type is NOT accepted (derived server-side)."""

    pair = serializers.CharField(help_text="e.g. BTC/USDT, btc-usdt, EUR_USD")
    side = serializers.ChoiceField(choices=Trade.Side.choices)
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, help_text="Margin in USD (max 2 dp).")
    leverage = serializers.IntegerField()
    take_profit_pct = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    stop_loss_pct = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    duration_hours = serializers.IntegerField(help_text="Exactly 6, 12 or 24.")

    def validate_pair(self, value):
        try:
            return normalize_pair(value)[0]
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_amount(self, value):
        minimum = Decimal(settings.TRADE_MIN_AMOUNT)
        if value < minimum:
            raise serializers.ValidationError(f"Amount must be at least {minimum:.2f}.")
        if value > Decimal("1000000"):
            raise serializers.ValidationError("Amount must not exceed 1,000,000.00.")
        return value

    def validate_leverage(self, value):
        if value < 1 or value > settings.TRADE_MAX_LEVERAGE:
            raise serializers.ValidationError(f"Leverage must be between 1 and {settings.TRADE_MAX_LEVERAGE}.")
        return value

    def validate_take_profit_pct(self, value):
        if value is None:
            return value
        if value <= 0:
            raise serializers.ValidationError("Take profit must be greater than 0.")
        if value > Decimal("10000"):
            raise serializers.ValidationError("Take profit must not exceed 10000.")
        return value

    def validate_stop_loss_pct(self, value):
        if value is None:
            return value
        if value <= 0:
            raise serializers.ValidationError("Stop loss must be greater than 0.")
        if value > Decimal("100"):
            raise serializers.ValidationError("Stop loss must not exceed 100.")
        return value

    def validate_duration_hours(self, value):
        if value not in DURATION_CHOICES:
            raise serializers.ValidationError("Duration must be exactly 6, 12 or 24 hours.")
        return value


class TradeSerializer(serializers.ModelSerializer):
    """
    Public trade representation. Fields are an explicit allow-list:
    planned_outcome / planned_pnl_pct / sequence / block / slot are NEVER included.
    """

    time_remaining_seconds = serializers.SerializerMethodField()
    progress_pct = serializers.SerializerMethodField()

    class Meta:
        model = Trade
        fields = (
            "id", "reference", "pair", "market_type", "side", "amount", "leverage",
            "take_profit_pct", "stop_loss_pct", "duration_hours", "status",
            "opened_at", "scheduled_close_at", "time_remaining_seconds", "progress_pct",
            "closed_at", "close_reason", "outcome", "pnl_pct", "pnl_amount",
        )
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.INT)
    def get_time_remaining_seconds(self, obj):
        if obj.status != Trade.Status.OPEN:
            return 0
        return max(0, int(round((obj.scheduled_close_at - timezone.now()).total_seconds())))

    @extend_schema_field(OpenApiTypes.FLOAT)
    def get_progress_pct(self, obj):
        if obj.status != Trade.Status.OPEN:
            return 100.0
        total = (obj.scheduled_close_at - obj.opened_at).total_seconds()
        if total <= 0:
            return 100.0
        elapsed = (timezone.now() - obj.opened_at).total_seconds()
        return round(min(100.0, max(0.0, elapsed / total * 100)), 2)


class TradeFilterSerializer(serializers.Serializer):
    """Query params for GET /api/trades/."""

    status = serializers.ChoiceField(choices=Trade.Status.choices, required=False)
    side = serializers.ChoiceField(choices=Trade.Side.choices, required=False)
    market_type = serializers.ChoiceField(choices=Trade.MarketType.choices, required=False)
    pair = serializers.CharField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    ordering = serializers.ChoiceField(choices=["-opened_at", "opened_at"], required=False, default="-opened_at")

    def validate_pair(self, value):
        try:
            return normalize_pair(value)[0]
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_from": ["date_from must not be after date_to."]})
        return attrs
