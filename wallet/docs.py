"""Reusable OpenAPI examples + an error helper for the Wallet endpoints."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

# ---------------------------------------------------------------------------
# Error helper (several wallet statuses carry multiple codes and a `data` payload)
# ---------------------------------------------------------------------------
_ERROR_SERIALIZER = inline_serializer(
    name="WalletErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "code": serializers.CharField(),
        "errors": serializers.DictField(
            child=serializers.ListField(child=serializers.CharField()), required=False
        ),
        "data": serializers.JSONField(allow_null=True, required=False),
    },
)


def wallet_error(description, *variants):
    """variants: dicts with code, message and optional errors / data."""
    examples = [
        OpenApiExample(
            v["code"],
            value={
                "success": False, "message": v["message"], "code": v["code"],
                "errors": v.get("errors") or {}, "data": v.get("data"),
            },
            response_only=True,
        )
        for v in variants
    ]
    return OpenApiResponse(response=_ERROR_SERIALIZER, description=description, examples=examples)


UNAUTH = wallet_error(
    "Missing, invalid or expired access token.",
    {"code": "not_authenticated", "message": "Authentication credentials were not provided."},
    {"code": "token_not_valid", "message": "Given token not valid for any token type."},
)
KYC_REQUIRED = wallet_error(
    "Identity verification (KYC) is not approved.",
    {
        "code": "kyc_required",
        "message": "Complete identity verification (KYC) and wait for approval before using this feature.",
        "data": {"kyc_status": "pending"},
    },
)
THROTTLED = wallet_error(
    "Too many requests.",
    {"code": "throttled", "message": "Request was throttled.", "data": {"retry_after_seconds": 42}},
)
NOT_FOUND = wallet_error("Unknown id, or it belongs to another user.", {"code": "not_found", "message": "Not found."})
VALIDATION = wallet_error(
    "One or more fields are invalid.",
    {
        "code": "validation_error",
        "message": "Please correct the highlighted fields.",
        "errors": {"network": ['"ton" is not a valid choice.']},
    },
)
CRON_FORBIDDEN = wallet_error(
    "Wrong or missing X-Cron-Secret header.",
    {"code": "invalid_cron_secret", "message": "Invalid or missing cron secret."},
)

BALANCES = {
    "balance": "1250.00", "locked_balance": "300.00",
    "pending_withdrawal_balance": "100.00", "available_balance": "850.00",
}

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------
WALLET_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {**BALANCES, "currency": "USD", "updated_at": "2026-10-22T14:30:00Z"},
}

CONFIG_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {
        "rate": "1.000000", "currency": "USD", "min_deposit_usd": "10.00", "min_withdrawal_usd": "20.00",
        "networks": [
            {"value": "bep20", "label": "USDT (BEP20 – BNB Smart Chain)",
             "deposit_address": "0x1234567890abcdef1234567890abcdef12345678",
             "address_regex": "^0x[a-fA-F0-9]{40}$"},
            {"value": "erc20", "label": "USDT (ERC20 – Ethereum)", "deposit_address": None,
             "address_regex": "^0x[a-fA-F0-9]{40}$"},
            {"value": "trc20", "label": "USDT (TRC20 – TRON)",
             "deposit_address": "TXyZ1234567890abcdefghijkmnopqrst",
             "address_regex": "^T[1-9A-HJ-NP-Za-km-z]{33}$"},
        ],
        "withdrawal_request_expiry_minutes": 5,
    },
}

DEPOSIT_REQUEST = {"usdt_amount": "500.000000", "network": "trc20", "tx_hash": "optional-transaction-id"}

DEPOSIT_DATA = {
    "id": "7b0f3c1e-2a4d-4e8f-9c11-5d6a7b8c9d01", "reference": "DEP-AB12CD34", "network": "trc20",
    "usdt_amount": "500.000000", "usd_amount": "500.00", "rate": "1.000000",
    "deposit_address": "TXyZ1234567890abcdefghijkmnopqrst", "tx_hash": None, "status": "pending",
    "decline_reason": None, "created_at": "2026-10-22T14:30:00Z", "processed_at": None,
}
DEPOSIT_CREATED_EXAMPLE = {
    "success": True,
    "message": "Deposit request created. Send exactly 500 USDT on TRC20 to the address shown.",
    "data": DEPOSIT_DATA,
}
DEPOSIT_DETAIL_EXAMPLE = {"success": True, "message": "Success", "data": DEPOSIT_DATA}
DEPOSIT_CANCELLED_EXAMPLE = {
    "success": True, "message": "Deposit cancelled.", "data": {**DEPOSIT_DATA, "status": "cancelled"},
}
DEPOSIT_LIST_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {"count": 1, "next": None, "previous": None, "results": [DEPOSIT_DATA]},
}

WITHDRAWAL_REQUEST = {
    "network": "bep20", "address": "0x1234567890abcdef1234567890abcdef12345678", "usd_amount": "100.00",
}
WITHDRAWAL_DATA = {
    "id": "c2d3e4f5-0a1b-4c2d-8e3f-9a0b1c2d3e4f", "reference": "WDR-AB12CD34", "status": "awaiting_otp",
    "network": "bep20", "address": "0x1234567890abcdef1234567890abcdef12345678",
    "usd_amount": "100.00", "usdt_amount": "100.000000", "rate": "1.000000",
    "expires_at": "2026-10-22T14:35:00Z", "confirmed_at": None, "admin_tx_hash": None,
    "failure_reason": None, "created_at": "2026-10-22T14:30:00Z", "processed_at": None,
}
WITHDRAWAL_CREATED_EXAMPLE = {
    "success": True,
    "message": "We've sent a 6-digit code to your email to confirm this withdrawal.",
    "data": {**WITHDRAWAL_DATA, "otp_sent": True, "expires_in_minutes": 5},
}
WITHDRAWAL_CONFIRMED_EXAMPLE = {
    "success": True,
    "message": "Withdrawal request submitted. It will be processed shortly.",
    "data": {
        **WITHDRAWAL_DATA, "status": "pending", "expires_at": None, "confirmed_at": "2026-10-22T14:32:00Z",
        "balances": {**BALANCES, "pending_withdrawal_balance": "200.00", "available_balance": "750.00"},
    },
}
WITHDRAWAL_DETAIL_EXAMPLE = {"success": True, "message": "Success", "data": WITHDRAWAL_DATA}
WITHDRAWAL_RESEND_EXAMPLE = {
    "success": True, "message": "A new code has been sent to your email.",
    "data": {"otp_sent": True, "expires_in_minutes": 5},
}
WITHDRAWAL_CANCELLED_EXAMPLE = {
    "success": True, "message": "Withdrawal cancelled.", "data": {**WITHDRAWAL_DATA, "status": "cancelled", "expires_at": None},
}
WITHDRAWAL_LIST_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {"count": 1, "next": None, "previous": None, "results": [WITHDRAWAL_DATA]},
}
CONFIRM_REQUEST = {"otp": "123456"}

TRANSACTIONS_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {
        "count": 1, "next": None, "previous": None,
        "results": [{
            "id": "a1b2c3d4-1111-4222-8333-444455556666", "entry_type": "deposit", "amount": "500.00",
            "balance_delta": "500.00", "locked_delta": "0.00", "pending_delta": "0.00",
            "balance_after": "500.00", "reference": "deposit:7b0f3c1e-2a4d-4e8f-9c11-5d6a7b8c9d01",
            "description": "Deposit DEP-AB12CD34", "created_at": "2026-10-22T16:00:00Z",
        }],
    },
}

CRON_EXAMPLE = {"success": True, "message": "Expired pending withdrawals.", "data": {"expired_withdrawals": 3}}
