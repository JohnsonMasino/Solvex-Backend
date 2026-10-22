import re
from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .models import (
    Deposit, DepositStatus, EntryType, Network, Wallet, WalletTransaction,
    Withdrawal, WithdrawalStatus,
)

_TX_HASH_RE = re.compile(r"^(0x)?[A-Za-z0-9]+$")


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class DepositCreateSerializer(serializers.Serializer):
    usdt_amount = serializers.DecimalField(
        max_digits=24, decimal_places=6,
        min_value=Decimal("0.000001"), max_value=Decimal("1000000"),
        error_messages={
            "min_value": "Amount must be greater than 0.",
            "max_value": "Maximum deposit is 1,000,000 USDT per request.",
        },
    )
    network = serializers.ChoiceField(choices=Network.choices)
    tx_hash = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate_tx_hash(self, value):
        value = (value or "").strip()
        if not value:
            return ""
        if not (6 <= len(value) <= 100) or not _TX_HASH_RE.match(value):
            raise serializers.ValidationError(
                "Transaction hash must be 6–100 letters/digits (optionally starting with 0x)."
            )
        return value.lower()


class WithdrawalCreateSerializer(serializers.Serializer):
    network = serializers.ChoiceField(choices=Network.choices)
    address = serializers.CharField(max_length=100)
    usd_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01"),
        error_messages={"min_value": "Amount must be greater than 0."},
    )


class ConfirmWithdrawalSerializer(serializers.Serializer):
    otp = serializers.CharField(max_length=12)

    def validate_otp(self, value):
        value = value.strip()
        if len(value) != settings.OTP_LENGTH or not value.isdigit():
            raise serializers.ValidationError(f"Enter the {settings.OTP_LENGTH}-digit code sent to your email.")
        return value


class DepositFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DepositStatus.choices, required=False)


class WithdrawalFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=WithdrawalStatus.choices, required=False)


class TransactionFilterSerializer(serializers.Serializer):
    entry_type = serializers.ChoiceField(choices=EntryType.choices, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        start, end = attrs.get("date_from"), attrs.get("date_to")
        if start and end and start > end:
            raise serializers.ValidationError({"date_from": "date_from must not be after date_to."})
        return attrs


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class WalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = (
            "balance", "locked_balance", "pending_withdrawal_balance",
            "available_balance", "currency", "updated_at",
        )
        read_only_fields = fields

    def get_currency(self, obj) -> str:
        return "USD"


class DepositSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deposit
        fields = (
            "id", "reference", "network", "usdt_amount", "usd_amount", "rate",
            "deposit_address", "tx_hash", "status", "decline_reason",
            "created_at", "processed_at",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.status != DepositStatus.DECLINED:
            data["decline_reason"] = None
        data["tx_hash"] = data["tx_hash"] or None
        return data


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Withdrawal
        fields = (
            "id", "reference", "status", "network", "address", "usd_amount",
            "usdt_amount", "rate", "expires_at", "confirmed_at", "admin_tx_hash",
            "failure_reason", "created_at", "processed_at",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.status != WithdrawalStatus.AWAITING_OTP:
            data["expires_at"] = None
        if instance.status != WithdrawalStatus.FAILED:
            data["failure_reason"] = None
        data["admin_tx_hash"] = data["admin_tx_hash"] or None
        return data


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = (
            "id", "entry_type", "amount", "balance_delta", "locked_delta", "pending_delta",
            "balance_after", "reference", "description", "created_at",
        )
        read_only_fields = fields
