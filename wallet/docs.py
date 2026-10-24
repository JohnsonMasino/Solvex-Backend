"""Reusable OpenAPI examples + an error helper for the Wallet endpoints."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

# ---------------------------------------------------------------------------
# Frontend guide: deposits (rendered as markdown in Swagger on the deposit + config endpoints)
# ---------------------------------------------------------------------------
LIVE_RATE_GUIDE = """

---
### Frontend notes: live rates & multi-asset deposits

**What changed**
- Deposits support **five networks / three assets**: `bep20`, `erc20`, `trc20` (USDT), `btc` (BTC), `eth` (ETH).
- USD rates are **not fixed**. For USDT, BTC and ETH the backend uses the **live market price** at the moment a request is made. The server tries several free price providers in order (CoinGecko, CryptoCompare, Coinbase, Kraken, Bitstamp), so one provider being down does not break anything. Prices are cached on the server for `rate_refresh_seconds`.
- Deposit request body: send **`amount`** (in the asset's own units) instead of `usdt_amount`. `usdt_amount` is still accepted on USDT networks for now but is deprecated.
- Deposit responses: `usdt_amount` is replaced by **`amount`** plus **`asset`** (`usdt` | `btc` | `eth`). `rate` is *USD per 1 unit of `asset`*.
- **Withdrawals now use the live USDT rate too, and charge a fee.** See the "Withdrawals" notes on the withdrawal endpoints. Withdrawals are still USDT only (`withdrawal_enabled` is `false` for `btc` / `eth`).

**How to handle the live rate**
1. Load the wallet config when the deposit screen opens, then **re-fetch it every `rate_refresh_seconds`** (default 30) while the screen is visible. Pause polling when the tab is hidden. Do not cache rates in localStorage.
2. Show the user an **estimate**: `usd ≈ amount × networks[].rate`. Label it "Estimated" and show `rate_updated_at` ("Rate updated 12s ago").
3. **The server's rate is the one that counts.** `POST` re-prices at submit time and freezes the result. Always show `data.rate` and `data.usd_amount` from the response on the confirmation screen and deposit detail, and never recompute them client-side. Tell the user the final USD credit can differ slightly from the estimate.
4. **Per-network switches:** if `deposit_enabled` is `false` (no company address or no live rate right now), disable that network and show "Temporarily unavailable". `rate` / `rate_updated_at` are `null` in that case.
5. **Errors to handle on `POST` deposit:**
   - `503 rate_unavailable`: the live rate couldn't be fetched. Show "Rate temporarily unavailable, please try again in a moment" and let the user retry. Do not treat it as a fatal error.
   - `400 deposit_below_minimum`: the minimum is checked in USD **at the live rate**, so an amount close to the minimum can flip between valid and invalid. Show the server message.
   - `400 validation_error` on `amount`: too many decimals for the asset (`decimals` in config: USDT 6, BTC 8, ETH 8) or above the per-request maximum.
6. The amount the user must send is `data.amount` in `data.asset` on `data.network`. The crypto amount is fixed. Only the USD credited depends on the rate.
7. `rate` at the top level of the config response is **deprecated** (USDT only). Use `networks[].rate`.
"""

# ---------------------------------------------------------------------------
# Frontend guide: withdrawals (live rate + fee)
# ---------------------------------------------------------------------------
WITHDRAWAL_FEE_GUIDE = """

---
### Frontend notes: withdrawal fee & live USDT rate

**What changed**
- Every withdrawal now carries a **fee** (`withdrawal_fee_percent` in the wallet config, currently **1.5%**). The fee is **added on top** of the amount the user wants to receive.
- The USDT/USD rate is **live** (no longer a fixed 1:1 peg). It is the same `networks[].rate` the config returns for the USDT networks (`bep20`, `erc20`, `trc20`), and it is frozen on the withdrawal when the request is created.
- Withdrawal objects have three new fields: `fee_percent`, `fee_usd`, `total_usd`.

**What each field means**

| Field | Meaning | Show it as |
|---|---|---|
| `usd_amount` | USD value the user **receives** (what they typed in) | "You receive (USD)" |
| `usdt_amount` | Exact USDT sent to their address (`usd_amount ÷ rate`) | "You receive ≈ X USDT" |
| `fee_percent` | Fee rate applied, e.g. `1.50` | "Fee (1.5%)" |
| `fee_usd` | Fee in USD (`usd_amount × fee_percent ÷ 100`, rounded to 2 decimals) | "Fee" |
| `total_usd` | `usd_amount + fee_usd`. This is the amount that **leaves the user's balance** | "Total deducted from balance" |
| `rate` | Live USD per 1 USDT used for this withdrawal | "Rate: 1 USDT = $1.0002" |

Example: the user enters `100.00` with a 1.5% fee. They receive `100.00` USD worth of USDT, the fee is `1.50`, and `101.50` is deducted from their balance.

**Withdrawal form (before submitting)**
1. Load the wallet config. Read `withdrawal_fee_percent`, `min_withdrawal_usd` and the USDT network's `rate`. Re-fetch every `rate_refresh_seconds` while the screen is visible.
2. As the user types `usd_amount`, show an **estimate**:
   - `fee ≈ usd_amount × withdrawal_fee_percent ÷ 100` (2 decimals)
   - `total ≈ usd_amount + fee`
   - `usdt ≈ usd_amount ÷ rate`
   Label these "Estimated". The server's figures are final.
3. Validate locally for convenience only: `usd_amount >= min_withdrawal_usd` (the minimum applies to the amount the user **receives**, not to the total) and `total <= available_balance`.
4. **"Max" button:** the largest amount the user can receive is a bit less than their available balance because of the fee. Start from `available_balance ÷ (1 + withdrawal_fee_percent ÷ 100)`, round down to 2 decimals, and if `amount + fee` still exceeds `available_balance`, subtract 0.01 until it fits.
5. If a network has `withdrawal_enabled: false` (BTC/ETH, or no live USDT rate right now), disable it.

**After `POST /wallet/withdrawals/` (OTP screen)**
- Show the **server's** values from `data`: `usd_amount`, `fee_usd` (with `fee_percent`), `total_usd`, `usdt_amount` and `rate`. **Never recompute them client-side**; they are frozen.
- Make it clear the user is confirming a total deduction of `total_usd`.
- `expires_at` is the deadline to enter the code. After the OTP is confirmed, nothing is re-priced.

**When money actually moves**
1. *Request created:* nothing moves yet.
2. *OTP confirmed (`status: pending`):* `total_usd` is **reserved**. `pending_withdrawal_balance` goes up by `total_usd` and `available_balance` goes down by `total_usd`. `balance` is unchanged.
3. *Admin completes it (`status: completed`):* `total_usd` is **removed from `balance`** (amount + fee) and the reservation is cleared. One ledger entry of type `withdrawal` is written with `amount = total_usd`; its description reads like `Withdrawal WDR-AB12CD34 completed (100.00 + 1.50 fee)`.
4. *Admin marks it failed (`status: failed`):* the **whole** `total_usd` is released back to `available_balance`. **No fee is charged** on failed, cancelled or expired withdrawals.

**Errors to handle**
- `503 rate_unavailable` on `POST /wallet/withdrawals/`: the live rate couldn't be fetched. Show "Rate temporarily unavailable, please try again in a moment" and allow a retry.
- `400 withdrawal_below_minimum`: `usd_amount` is below `min_withdrawal_usd`.
- `400 insufficient_balance` / `400 funds_in_use`: the **total including the fee** doesn't fit. `data` contains the balances plus `total_usd` (what was needed). Show e.g. "You need $101.50 (amount + $1.50 fee) but only $90.00 is available."

**Older withdrawals:** withdrawals created before the fee existed return `fee_percent: "0.00"`, `fee_usd: "0.00"` and `total_usd` equal to `usd_amount`. Render them the same way (hide the fee line if `fee_usd` is `"0.00"` if you prefer).
"""

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
# insufficient_balance / funds_in_use carry the balances plus the amount+fee that was required.
BALANCES_WITH_TOTAL = {**BALANCES, "total_usd": "101.50"}

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------
WALLET_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {**BALANCES, "loan_outstanding": "0.00", "net_balance": "1250.00", "has_active_loan": False,
             "currency": "USD", "updated_at": "2026-10-22T14:30:00Z"},
}

CONFIG_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {
        "rate": "1.000200",  # deprecated: USDT only, use networks[].rate
        "currency": "USD", "min_deposit_usd": "10.00", "min_withdrawal_usd": "20.00",
        "withdrawal_fee_percent": "1.5",
        "rates_updated_at": "2026-10-22T14:29:48+00:00",
        "rate_refresh_seconds": 30,
        "networks": [
            {"value": "bep20", "label": "USDT (BEP20 – BNB Smart Chain)", "asset": "usdt", "decimals": 6,
             "rate": "1.000200", "rate_updated_at": "2026-10-22T14:29:48+00:00",
             "deposit_address": "0x1234567890abcdef1234567890abcdef12345678",
             "deposit_enabled": True, "withdrawal_enabled": True,
             "address_regex": "^0x[a-fA-F0-9]{40}$"},
            {"value": "erc20", "label": "USDT (ERC20 – Ethereum)", "asset": "usdt", "decimals": 6,
             "rate": "1.000200", "rate_updated_at": "2026-10-22T14:29:48+00:00",
             "deposit_address": None,
             "deposit_enabled": False, "withdrawal_enabled": True,
             "address_regex": "^0x[a-fA-F0-9]{40}$"},
            {"value": "trc20", "label": "USDT (TRC20 – TRON)", "asset": "usdt", "decimals": 6,
             "rate": "1.000200", "rate_updated_at": "2026-10-22T14:29:48+00:00",
             "deposit_address": "TXyZ1234567890abcdefghijkmnopqrst",
             "deposit_enabled": True, "withdrawal_enabled": True,
             "address_regex": "^T[1-9A-HJ-NP-Za-km-z]{33}$"},
            {"value": "btc", "label": "BTC (Bitcoin)", "asset": "btc", "decimals": 8,
             "rate": "95123.450000", "rate_updated_at": "2026-10-22T14:29:48+00:00",
             "deposit_address": "bc1qexampleaddressxxxxxxxxxxxxxxxxxxxxxxxx",
             "deposit_enabled": True, "withdrawal_enabled": False,
             "address_regex": "^(bc1[02-9ac-hj-np-z]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$"},
            {"value": "eth", "label": "ETH (Ethereum)", "asset": "eth", "decimals": 8,
             "rate": None, "rate_updated_at": None,
             "deposit_address": "0x1234567890abcdef1234567890abcdef12345678",
             "deposit_enabled": False, "withdrawal_enabled": False,
             "address_regex": "^0x[a-fA-F0-9]{40}$"},
        ],
        "withdrawal_request_expiry_minutes": 5,
    },
}

DEPOSIT_REQUEST = {"amount": "500", "network": "trc20", "tx_hash": "optional-transaction-id"}
DEPOSIT_REQUEST_BTC = {"amount": "0.005", "network": "btc", "tx_hash": "optional-transaction-id"}

# `rate` and `usd_amount` are frozen at creation from the live rate at that moment.
DEPOSIT_DATA = {
    "id": "7b0f3c1e-2a4d-4e8f-9c11-5d6a7b8c9d01", "reference": "DEP-AB12CD34", "network": "trc20",
    "asset": "usdt", "amount": "500.00000000", "usd_amount": "500.10", "rate": "1.000200",
    "deposit_address": "TXyZ1234567890abcdefghijkmnopqrst", "tx_hash": None, "status": "pending",
    "decline_reason": None, "created_at": "2026-10-22T14:30:00Z", "processed_at": None,
}
DEPOSIT_CREATED_EXAMPLE = {
    "success": True,
    "message": "Deposit request created. Send exactly 500 USDT on the TRC20 network to the address shown.",
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

# `usd_amount` is what the user wants to RECEIVE. The 1.5% fee is added on top by the server.
WITHDRAWAL_REQUEST = {
    "network": "bep20", "address": "0x1234567890abcdef1234567890abcdef12345678", "usd_amount": "100.00",
}
# 100.00 requested -> fee 1.50 (1.5%) -> 101.50 deducted from the balance; rate is the live USDT price.
WITHDRAWAL_DATA = {
    "id": "c2d3e4f5-0a1b-4c2d-8e3f-9a0b1c2d3e4f", "reference": "WDR-AB12CD34", "status": "awaiting_otp",
    "network": "bep20", "address": "0x1234567890abcdef1234567890abcdef12345678",
    "usd_amount": "100.00", "fee_percent": "1.50", "fee_usd": "1.50", "total_usd": "101.50",
    "usdt_amount": "99.980003", "rate": "1.000200",
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
        # total_usd (101.50) is now reserved: pending +101.50, available -101.50, balance unchanged
        "balances": {**BALANCES, "pending_withdrawal_balance": "201.50", "available_balance": "748.50"},
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
        "count": 2, "next": None, "previous": None,
        "results": [
            {
                "id": "b7c8d9e0-2222-4333-8444-555566667777", "entry_type": "withdrawal", "amount": "101.50",
                "balance_delta": "-101.50", "locked_delta": "0.00", "pending_delta": "-101.50",
                "balance_after": "398.60", "reference": "withdrawal:c2d3e4f5-0a1b-4c2d-8e3f-9a0b1c2d3e4f",
                "description": "Withdrawal WDR-AB12CD34 completed (100.00 + 1.50 fee)",
                "created_at": "2026-10-22T17:00:00Z",
            },
            {
                "id": "a1b2c3d4-1111-4222-8333-444455556666", "entry_type": "deposit", "amount": "500.10",
                "balance_delta": "500.10", "locked_delta": "0.00", "pending_delta": "0.00",
                "balance_after": "500.10", "reference": "deposit:7b0f3c1e-2a4d-4e8f-9c11-5d6a7b8c9d01",
                "description": "Deposit DEP-AB12CD34 (500 USDT)", "created_at": "2026-10-22T16:00:00Z",
            },
        ],
    },
}

CRON_EXAMPLE = {"success": True, "message": "Expired pending withdrawals.", "data": {"expired_withdrawals": 3}}

# WalletSummaryView