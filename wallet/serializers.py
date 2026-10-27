import re
from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .models import (
    ASSET_DECIMALS, NETWORK_ASSET, WITHDRAWAL_NETWORK_CHOICES,
    Asset, Deposit, DepositStatus, EntryType, Network, Wallet, WalletTransaction,
    Withdrawal, WithdrawalStatus,
)

_TX_HASH_RE = re.compile(r"^(0x)?[A-Za-z0-9]+$")

# Per-request ceilings, in each asset's own units. Adjust to taste.
MAX_DEPOSIT_AMOUNT = {
    Asset.USDT: Decimal("1000000"),
    Asset.BTC: Decimal("100"),
    Asset.ETH: Decimal("1000"),
}


def _amount_field():
    return serializers.DecimalField(
        max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"), required=False,
        error_messages={"min_value": "Amount must be greater than 0."},
    )


def _effective_created_at(obj) -> str:
    """The admin-chosen transaction time if one was set, otherwise the real creation time."""
    return serializers.DateTimeField().to_representation(obj.effective_at)


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class DepositCreateSerializer(serializers.Serializer):
    network = serializers.ChoiceField(choices=Network.choices)
    amount = _amount_field()
    # Deprecated alias for `amount` (USDT networks only). Remove once the frontend sends `amount`.
    usdt_amount = _amount_field()
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

    def validate(self, attrs):
        asset = NETWORK_ASSET[Network(attrs["network"])]
        amount, legacy = attrs.get("amount"), attrs.get("usdt_amount")

        if legacy is not None and asset != Asset.USDT:
            raise serializers.ValidationError(
                {"usdt_amount": "usdt_amount only works with USDT networks. Send 'amount' instead."}
            )
        if amount is None and legacy is None:
            raise serializers.ValidationError({"amount": "This field is required."})
        if amount is None:
            amount = legacy
        elif legacy is not None and legacy != amount:
            raise serializers.ValidationError({"usdt_amount": "Conflicts with 'amount'. Send only 'amount'."})

        places = ASSET_DECIMALS[asset]
        exponent = amount.normalize().as_tuple().exponent
        if exponent < -places:
            raise serializers.ValidationError(
                {"amount": f"{asset.label} supports at most {places} decimal places."}
            )
        maximum = MAX_DEPOSIT_AMOUNT[asset]
        if amount > maximum:
            raise serializers.ValidationError(
                {"amount": f"Maximum deposit is {maximum:,} {asset.label} per request."}
            )

        attrs["amount"] = amount
        attrs.pop("usdt_amount", None)  # the legacy alias is folded into `amount`
        return attrs


class WithdrawalCreateSerializer(serializers.Serializer):
    network = serializers.ChoiceField(choices=WITHDRAWAL_NETWORK_CHOICES)
    address = serializers.CharField(max_length=100)
    # The USD value the user wants to RECEIVE. The withdrawal fee is added on top of this amount.
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
    loan_outstanding = serializers.SerializerMethodField()
    net_balance = serializers.SerializerMethodField()
    has_active_loan = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = (
            "balance", "locked_balance", "pending_withdrawal_balance",
            "available_balance", "loan_outstanding", "net_balance", "has_active_loan",
            "currency", "updated_at",
        )
        read_only_fields = fields

    def get_currency(self, obj) -> str:
        return "USD"

    def _loan(self, obj):
        if not hasattr(obj, "_loan_snapshot"):
            from loan.services import account_snapshot
            obj._loan_snapshot = account_snapshot(obj.user, wallet=obj)
        return obj._loan_snapshot

    def get_loan_outstanding(self, obj) -> str:
        return self._loan(obj)["loan_outstanding"]

    def get_net_balance(self, obj) -> str:
        return self._loan(obj)["net_balance"]

    def get_has_active_loan(self, obj) -> bool:
        return self._loan(obj)["has_active_loan"]


class DepositSerializer(serializers.ModelSerializer):
    # Shows the admin-chosen transaction time when one was set, otherwise the real creation time.
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = Deposit
        fields = (
            "id", "reference", "network", "asset", "amount", "usd_amount", "rate",
            "deposit_address", "tx_hash", "status", "decline_reason",
            "created_at", "processed_at",
        )
        read_only_fields = fields

    def get_created_at(self, obj) -> str:
        return _effective_created_at(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.status != DepositStatus.DECLINED:
            data["decline_reason"] = None
        data["tx_hash"] = data["tx_hash"] or None
        return data


class WithdrawalSerializer(serializers.ModelSerializer):
    """
    usd_amount   = what the user receives (USD value)
    fee_percent  = fee rate frozen on this withdrawal
    fee_usd      = fee in USD
    total_usd    = usd_amount + fee_usd = what leaves the user's balance
    rate         = live USD per 1 USDT, frozen at creation
    usdt_amount  = exact USDT sent to the user's address
    created_at   = admin-chosen transaction time if set, otherwise the real creation time
    """

    created_at = serializers.SerializerMethodField()

    class Meta:
        model = Withdrawal
        fields = (
            "id", "reference", "status", "network", "address",
            "usd_amount", "fee_percent", "fee_usd", "total_usd",
            "usdt_amount", "rate", "expires_at", "confirmed_at", "admin_tx_hash",
            "failure_reason", "created_at", "processed_at",
        )
        read_only_fields = fields

    def get_created_at(self, obj) -> str:
        return _effective_created_at(obj)

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