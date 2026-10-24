"""OpenAPI guide + examples for the Loan endpoints (rendered as markdown in Swagger)."""
from wallet import docs as wallet_docs
from wallet.docs import wallet_error

UNAUTH = wallet_docs.UNAUTH
KYC_REQUIRED = wallet_docs.KYC_REQUIRED
THROTTLED = wallet_docs.THROTTLED
NOT_FOUND = wallet_docs.NOT_FOUND
VALIDATION = wallet_docs.VALIDATION
CRON_FORBIDDEN = wallet_docs.CRON_FORBIDDEN
BALANCES = wallet_docs.BALANCES

# ---------------------------------------------------------------------------
# Frontend guide
# ---------------------------------------------------------------------------
LOAN_GUIDE = """

---
### Frontend notes: Loans (read this first)

**Lifecycle: a loan must be APPROVED by an admin**
```
user submits request ──> pending ──(admin approves)──> active ──(fully paid)──> repaid
                            ├──(admin rejects)──> rejected   (decline_reason is set)
                            └──(user cancels)───> cancelled
```
- `POST /loans/` only creates a **request** (`status: "pending"`). Nothing is owed, no interest runs and `net_balance` does **not** change yet.
- When an admin approves it, the loan becomes `active`, the clock starts (`started_at`, `halfway_at`, `due_at` are filled in) and the user gets an **approval email**. Rejected requests get an email with the reason (`decline_reason`).
- There is no push notification: re-fetch `GET /loans/current/` when the loan page opens (and optionally every 30-60 s while a request is `pending`).
- While a request is `pending` the user may cancel it with `POST /loans/{id}/cancel/`.

**The rules (all enforced by the server)**
- A user can request a loan only when their wallet `balance` is **below `max_eligible_balance_usd`** (currently **$50**).
- Loan size: from `min_loan_usd` up to **`max_loan_usd` ($10,000)**.
- **One open loan at a time**: a user cannot request a new loan while one is `pending` or `active`. After `repaid`, `rejected` or `cancelled` they can request again.
- Duration: `1_week` (7 days), `2_weeks` (14), `1_month` (30), `2_months` (60), `3_months` (90). Always render the list from `GET /loans/config/`, never hard-code it.
- **Interest is 0.2% of the original principal per day** within the term (`daily_rate_percent`). After the due date it becomes **1% per day** (`overdue_rate_percent`). It is *simple* interest on the principal, partial repayments do not reduce it. A day counts once a full 24 hours have passed since `started_at` (the approval time). The rates shown at request time are the ones frozen on the loan.
- Requesting a loan requires **KYC approved** (same as deposits and withdrawals).

**Interest maths (for estimates only, the server's numbers are final)**
```
interest_accrued = principal x 0.2% x min(days_elapsed, duration_days)
                 + principal x 1.0% x overdue_days
total_due        = principal + interest_accrued
outstanding      = total_due - amount_repaid
```
Example: $5,000 for 1 month. Interest is $10.00 per day. After 10 days: interest `100.00`, `total_due` `5100.00`. If the user already paid `500.00`, `outstanding` is `4600.00`.
For a preview on the request form: `daily_interest = amount x daily_interest_percent / 100`, and `total_if_repaid_on_due_date = amount + daily_interest x days` (use `days` from the chosen duration).

**IMPORTANT: which balance to show as "Account balance"**
The wallet summary (`GET /wallet/`) returns extra fields:

| Field | Meaning |
|---|---|
| `balance` | The real money in the wallet. Never negative. |
| `loan_outstanding` | What the user currently owes on the ACTIVE loan (`"0.00"` if none or only pending). |
| `net_balance` | `balance - loan_outstanding`. **Show this as the account balance. It can be negative**, e.g. `"-4970.00"`. Render the minus sign and use a warning/red style. |
| `has_active_loan` | `true` while an approved loan is owed. Use it to show a "Loan" banner / link. |

Example: user has $30, the admin approves a $5,000 loan -> `balance: 30.00`, `loan_outstanding: 5000.00`, `net_balance: -4970.00`.
Keep using `available_balance` for trade/withdraw validation, it is not affected by the loan.
`POST /loans/`, `GET /loans/current/` and `POST /loans/{id}/repay/` also return an `account` object with the same numbers.

**How repayment works (only for `active` loans)**
1. **Automatic (deposits).** When an admin completes a deposit, the credited USD is used **automatically** to pay the active loan, up to the amount owed. The wallet gets a `deposit` ledger entry and then a `loan_repayment` ledger entry (negative). Example: owing $1,000, user deposits $500 -> `outstanding` becomes `500.00` and `net_balance` shows `-500.00`. The original amount stays visible as `principal`. If the deposit is bigger than the debt, the loan closes (`status: repaid`) and the rest stays in `balance`. A deposit made while the loan is only `pending` is **not** used for the loan.
2. **Manual (Repay button).** Call `POST /loans/{id}/repay/`.
   - If the wallet has no available funds the server answers **400 `no_funds_to_repay`** with `data.action = "deposit"`. **Show "There is no money in your account balance. Deposit money to repay your loan" and navigate the user to the Deposit page.**
   - Otherwise it pays `min(available_balance, outstanding)` (or the optional `amount`) and returns the updated loan.
   - On a loan that is not `active` the server answers `409 loan_not_active` (hide the Repay button unless `status == "active"`).
3. Users cannot withdraw while a loan is `active` (`409 active_loan` on `POST /wallet/withdrawals/`). Show the same "repay first" message.

**What to build**
- *Request screen:* call `GET /loans/config/` (durations, limits, rates) and `GET /loans/eligibility/` (`eligible`, `reasons`). If `eligible` is `false` disable the form and map `reasons`: `loan_request_pending` -> "Your loan request is waiting for approval", `active_loan_exists` -> "You have an active loan", `balance_too_high` -> "Your balance must be below $50 to borrow". Submit with `POST /loans/` and then show the pending state.
- *Loan page:* call `GET /loans/current/` (returns the pending OR active loan, `data: null` if none). Branch on `status`:
  - `pending`: "Your request of `principal` for `duration_label` is awaiting approval" + a Cancel button.
  - `active`: `outstanding` as the big "Amount owed" number, `principal` / `interest_accrued` / `amount_repaid` / `duration_label` / `started_at` / `due_at` under "Loan details", a countdown from `days_remaining`, a red "Overdue" badge when `is_overdue` is `true` (then show `current_rate_percent`, now 1%), and the Repay button.
- *History:* `GET /loans/` (paginated, filter with `?status=`) and `GET /loans/{id}/` (includes `repayments`). For `rejected` loans show `decline_reason`.
- `outstanding` grows daily because interest accrues with time. Never compute it client-side.

**Emails the user receives:** request received, **loan approved**, loan declined (with reason), a reminder at **half of the loan period** (`halfway_at`), an **overdue notice** when `due_at` passes (interest moves to 1% per day), a payment-received email on every repayment, and a fully-repaid email.

**Statuses:** `pending`, `active` (owing), `repaid` (fully paid, `closed_at` set, interest frozen), `rejected`, `cancelled`.
**Money fields are strings** with 2 decimals, parse them as decimals, not floats. Timestamps are UTC ISO-8601. `started_at`, `halfway_at` and `due_at` are `null` until approval.
"""

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------
LOAN_DATA = {
    "id": "d4e5f6a7-1b2c-4d3e-8f90-a1b2c3d4e5f6", "reference": "LN-AB12CD34", "status": "active",
    "principal": "5000.00",
    "duration": "1_month", "duration_label": "1 month", "duration_days": 30,
    "daily_rate_percent": "0.20", "overdue_rate_percent": "1.00", "current_rate_percent": "0.20",
    "daily_interest_usd": "10.00",
    "requested_at": "2026-10-22T09:00:00Z", "processed_at": "2026-10-22T14:30:00Z",
    "started_at": "2026-10-22T14:30:00Z", "halfway_at": "2026-11-06T14:30:00Z",
    "due_at": "2026-11-21T14:30:00Z", "closed_at": None,
    "days_elapsed": 10, "days_remaining": 20, "overdue_days": 0, "is_overdue": False,
    "interest_accrued": "100.00", "total_due": "5100.00", "amount_repaid": "500.00", "outstanding": "4600.00",
    "credited_to_wallet": False, "decline_reason": None,
}
LOAN_PENDING_DATA = {
    **LOAN_DATA, "status": "pending", "processed_at": None, "started_at": None, "halfway_at": None,
    "due_at": None, "current_rate_percent": "0.20", "daily_interest_usd": "0.00",
    "days_elapsed": 0, "days_remaining": 0, "interest_accrued": "0.00", "total_due": "0.00",
    "amount_repaid": "0.00", "outstanding": "0.00",
}
LOAN_REJECTED_DATA = {
    **LOAN_PENDING_DATA, "status": "rejected", "processed_at": "2026-10-22T11:00:00Z",
    "decline_reason": "We could not approve this request at this time.",
}
LOAN_OVERDUE_DATA = {
    **LOAN_DATA, "days_elapsed": 33, "days_remaining": 0, "overdue_days": 3, "is_overdue": True,
    "current_rate_percent": "1.00", "daily_interest_usd": "50.00",
    "interest_accrued": "450.00", "total_due": "5450.00", "outstanding": "4950.00",
}
REPAYMENT_DATA = {
    "id": "e5f6a7b8-2c3d-4e4f-9a01-b2c3d4e5f6a7", "amount": "500.00", "source": "deposit",
    "outstanding_after": "4600.00", "created_at": "2026-11-01T09:00:00Z",
}
ACCOUNT_NO_LOAN = {"balance": "30.00", "loan_outstanding": "0.00", "net_balance": "30.00", "has_active_loan": False}
ACCOUNT_ACTIVE = {"balance": "30.00", "loan_outstanding": "5000.00", "net_balance": "-4970.00", "has_active_loan": True}
ACCOUNT_AFTER_REPAY = {"balance": "0.00", "loan_outstanding": "4600.00", "net_balance": "-4600.00", "has_active_loan": True}

CONFIG_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {
        "currency": "USD", "min_loan_usd": "10.00", "max_loan_usd": "10000.00",
        "max_eligible_balance_usd": "50.00",
        "daily_interest_percent": "0.2", "overdue_daily_interest_percent": "1",
        "durations": [
            {"value": "1_week", "label": "1 week", "days": 7},
            {"value": "2_weeks", "label": "2 weeks", "days": 14},
            {"value": "1_month", "label": "1 month", "days": 30},
            {"value": "2_months", "label": "2 months", "days": 60},
            {"value": "3_months", "label": "3 months", "days": 90},
        ],
        "requires_admin_approval": True,
        "principal_credited_to_wallet": False,
    },
}
ELIGIBLE_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {
        "eligible": True, "reasons": [], "balance": "30.00", "max_eligible_balance_usd": "50.00",
        "min_loan_usd": "10.00", "max_loan_usd": "10000.00", "open_loan_id": None, "open_loan_status": None,
    },
}
NOT_ELIGIBLE_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {
        "eligible": False, "reasons": ["loan_request_pending", "balance_too_high"], "balance": "120.00",
        "max_eligible_balance_usd": "50.00", "min_loan_usd": "10.00", "max_loan_usd": "10000.00",
        "open_loan_id": "d4e5f6a7-1b2c-4d3e-8f90-a1b2c3d4e5f6", "open_loan_status": "pending",
    },
}
LOAN_REQUEST = {"amount": "5000.00", "duration": "1_month"}
LOAN_REQUESTED_EXAMPLE = {
    "success": True,
    "message": "Your loan request has been submitted and is waiting for admin approval.",
    "data": {**LOAN_PENDING_DATA, "account": ACCOUNT_NO_LOAN},
}
LOAN_DETAIL_EXAMPLE = {
    "success": True, "message": "Success", "data": {**LOAN_DATA, "repayments": [REPAYMENT_DATA]},
}
LOAN_CURRENT_EXAMPLE = {"success": True, "message": "Success", "data": {**LOAN_DATA, "account": ACCOUNT_AFTER_REPAY}}
LOAN_CURRENT_PENDING_EXAMPLE = {"success": True, "message": "Success", "data": {**LOAN_PENDING_DATA, "account": ACCOUNT_NO_LOAN}}
LOAN_NONE_EXAMPLE = {"success": True, "message": "You have no open loan.", "data": None}
LOAN_LIST_EXAMPLE = {
    "success": True, "message": "Success",
    "data": {"count": 2, "next": None, "previous": None, "results": [LOAN_PENDING_DATA, LOAN_REJECTED_DATA]},
}
LOAN_CANCELLED_EXAMPLE = {
    "success": True, "message": "Loan request cancelled.",
    "data": {**LOAN_PENDING_DATA, "status": "cancelled"},
}
REPAY_REQUEST = {"amount": "100.00"}
REPAY_EXAMPLE = {
    "success": True, "message": "Payment received. Your loan balance is now 4600.00 USD.",
    "data": {**LOAN_DATA, "repayment": REPAYMENT_DATA, "account": ACCOUNT_AFTER_REPAY,
             "balances": {"balance": "0.00", "locked_balance": "0.00",
                          "pending_withdrawal_balance": "0.00", "available_balance": "0.00"}},
}
CRON_EXAMPLE = {
    "success": True, "message": "Processed loan reminders.",
    "data": {"halfway_reminders_sent": 2, "overdue_notices_sent": 1},
}