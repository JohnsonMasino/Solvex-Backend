"""OpenAPI examples + frontend guide for the Trading Signals endpoint."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

# ---------------------------------------------------------------------------
# Frontend guide (rendered as markdown in Swagger on GET /signals/eligibility/)
# ---------------------------------------------------------------------------
ELIGIBILITY_GUIDE = """
Tells the frontend whether the **logged-in user** may open the Trading Signal page.

The user is identified from the JWT. **Do not send an email or user id.** The backend reads the
account email from the token and checks it against the list admins maintain in
*Admin console > Trading Signals > Eligible emails*.

- Match is **case-insensitive** (`Jane@Mail.com` == `jane@mail.com`).
- Only rows with **Is active = ticked** count.
- Admin changes apply **immediately**. There is no cache. Always call this endpoint on every page open.
- A non-eligible user gets **HTTP 200** with `eligible: false`. This is not an error, so do not put it in your `catch`.
- Only real errors (`401`) arrive as error responses.

---
### Frontend flow: end to end

**1. Page load: check eligibility**

```js
// SignalsPage mount
const res = await api.get("/signals/eligibility/");
if (!res.data.data.eligible) {
  toast.error(res.data.data.message);   // "You are not eligible for a trading signal at this time."
  navigate("/dashboard");               // route back
  return;
}
// eligible => render the 4 plans
```

Show a loading state until this resolves. **Do not flash the plans** before the answer arrives.
On `401`, follow your normal refresh-token / logout flow.

**2. Render the 4 plans**

Plan names and USD amounts are defined **in the frontend** (a constant array). The backend does not
store plans, prices or signals. Changing a price needs no backend change.

**3. User clicks a plan: show the pop-up**

The modal must tell the user to **send the exact USD amount of the plan** and display the equivalent in
**USDT, BTC and ETH**:
usdt = planUsd / USDT_USD_RATE
btc = planUsd / BTC_USD_RATE
eth = planUsd / ETH_USD_RATE

Use the **same rate source the Deposit page uses** (the wallet rates/config endpoint), so the numbers in
the pop-up match what the Deposit page shows. Suggested display precision: USDT 2 dp, BTC 8 dp, ETH 6 dp.
Do not let the user edit the plan amount in the pop-up.

**4. Pop-up "Continue": go to Deposit with prefill**

Navigate to the Deposit page with the plan's USD amount and USDT preselected, for example:

```js
navigate(`/dashboard/deposit?currency=USDT&amount=${usdtAmount}`);
// or router state: navigate("/dashboard/deposit", { state: { currency: "USDT", amount: usdtAmount } })
```

The Deposit page should read these on mount, select **USDT** (already its default) and prefill the amount
field. Treat the prefill as a convenience only. The deposit endpoints still validate everything server-side.

**5. What the backend does NOT do**

This app only answers "is this email on the list?". It does not create orders, charge users, activate
signals or send signals. The payment is a normal wallet deposit.

---
### Admin guide (non-technical)

*Admin console > Trading Signals > Eligible emails*

| Task | How |
|---|---|
| Make a user eligible | **Add eligible email**, enter the exact email they registered with, **Save** |
| Remove a user | Open the row > **Delete**, or tick rows in the list and use *Delete selected* |
| Pause without deleting | Untick **Is active** (also toggleable directly in the list), or use the bulk actions |
| Edit an email | Open the row, change it, **Save** |
| Find someone | Use the search box (email or note) |

Duplicates are rejected, and `Jane@x.com` and `jane@x.com` count as the same email.
"""

ELIGIBLE_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {"eligible": True, "message": "You are eligible to activate a trading signal."},
}

NOT_ELIGIBLE_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {"eligible": False, "message": "You are not eligible for a trading signal at this time."},
}

# --------------------------------------------------------------------------- #
# Error docs
# --------------------------------------------------------------------------- #
_ERROR_SERIALIZER = inline_serializer(
    name="SignalsErrorEnvelope",
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


def signals_error(description, *variants):
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


UNAUTHENTICATED = signals_error(
    "Missing, invalid or expired access token.",
    {"code": "not_authenticated", "message": "Authentication credentials were not provided."},
    {"code": "token_not_valid", "message": "Given token not valid for any token type."},
)