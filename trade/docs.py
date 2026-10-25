"""Reusable OpenAPI examples for the trade app. No example shows admin_note, settled_by or any planned_* field."""
from core.openapi import error_response_doc

BALANCES = {"balance": "1250.00", "locked_balance": "300.00", "available_balance": "950.00"}

OPEN_TRADE = {
    "id": "6f1c1c1e-3a52-4c63-9d0e-2b6a6c1d9a10",
    "reference": "TRD-AB12CD34",
    "pair": "BTC/USDT",
    "market_type": "crypto",
    "side": "buy",
    "amount": "300.00",
    "leverage": 10,
    "take_profit_pct": "5.00",
    "stop_loss_pct": "2.00",
    "duration_hours": 6,
    "status": "open",
    "opened_at": "2026-10-22T14:30:00Z",
    "scheduled_close_at": "2026-10-22T20:30:00Z",
    "time_remaining_seconds": 21600,
    "progress_pct": 0.0,
    "awaiting_settlement": False,
    "closed_at": None,
    "close_reason": None,
    "outcome": None,
    "pnl_pct": None,
    "pnl_amount": None,
}

# Time is up, waiting for staff to set the result.
AWAITING_TRADE = {
    **OPEN_TRADE,
    "id": "3b7e5a42-91c4-4d0b-b8a3-6c0f2d4e1a77",
    "reference": "TRD-KM45PQ12",
    "pair": "ETH/USDT",
    "side": "sell",
    "time_remaining_seconds": 0,
    "progress_pct": 100.0,
    "awaiting_settlement": True,
}

CLOSED_TRADE = {
    **OPEN_TRADE,
    "id": "0a9d3b57-7c1e-4f0a-8d11-5e2f6a7b8c90",
    "reference": "TRD-ZX98YW76",
    "pair": "EUR/USD",
    "market_type": "forex",
    "side": "sell",
    "amount": "300.00",
    "status": "closed",
    "time_remaining_seconds": 0,
    "progress_pct": 100.0,
    "awaiting_settlement": False,
    "closed_at": "2026-10-22T21:05:10Z",
    "close_reason": "admin",
    "outcome": "profit",
    "pnl_pct": "0.80",
    "pnl_amount": "2.40",
}

OPEN_REQUEST = {
    "pair": "BTC/USDT",
    "side": "buy",
    "amount": "300.00",
    "leverage": 10,
    "take_profit_pct": "5.00",
    "stop_loss_pct": "2.00",
    "duration_hours": 6,
}


def envelope(data, message="Success"):
    return {"success": True, "message": message, "data": data}


def err_unauthenticated():
    return error_response_doc(
        "Missing, invalid or expired access token.",
        code="not_authenticated",
        message="Authentication credentials were not provided.",
    )


def err_throttled():
    return error_response_doc("Too many requests.", code="throttled", message="Request was throttled.")


def err_kyc():
    return error_response_doc(
        "KYC (Tier 2) is required to open trades.",
        code="kyc_required",
        message="Complete identity verification (KYC) and wait for approval before using this feature.",
    )