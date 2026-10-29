"""Reusable OpenAPI examples + a small error helper for the KYC endpoints."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

from .models import (
    AddressEntryMethod, AnnualIncomeRange, EmploymentStatus, ExpectedAnnualVolume,
    IDType, Sex, SourceOfIncome, TransactionFrequency, TypicalTransactionSize,
)

# ---------------------------------------------------------------------------
# Frontend guide: what KYC unlocks (rendered as markdown in Swagger on GET /kyc/status/)
# ---------------------------------------------------------------------------
ACCESS_GUIDE = """

---
### Frontend notes: what a user can do before / after KYC

**What changed:** KYC is **no longer required to deposit**. A user can deposit as soon as they have registered and verified their email. KYC is still required for everything else.

| Feature | Registered, no KYC (Tier 1) | KYC approved (Tier 2) |
|---|---|---|
| Create / list / cancel deposits | ✅ | ✅ |
| View wallet, ledger, wallet config | ✅ | ✅ |
| Withdraw (`POST /wallet/withdrawals/`) | ❌ `403 kyc_required` | ✅ |
| Open trades (`POST /trades/`) | ❌ `403 kyc_required` | ✅ |
| Request a loan (`POST /loans/`) | ❌ `403 kyc_required` | ✅ |

**Use `data.access` from this endpoint to drive the UI.** Do not re-derive it from `tier`:

```json
"access": {
  "can_deposit": true,
  "can_withdraw": false,
  "can_trade": false,
  "can_request_loan": false
}
```

- `can_deposit` is always `true` for a logged-in user. Remove any "complete KYC to deposit" gate or banner on the Deposit page.
- When `can_withdraw` / `can_trade` / `can_request_loan` is `false`, keep the buttons visible but **disabled (or redirect)** with a prompt such as *"Verify your identity to unlock withdrawals"* that links to the KYC form. Branch the wording on `kyc_status` (`not_submitted`, `pending`, `rejected`, `approved`).
- A Tier 1 user **can have a real balance** (from completed deposits). Show it normally. They just cannot withdraw or trade it until KYC is approved. Make that clear to avoid support tickets.
- Still handle `403 kyc_required` on the gated endpoints (the token can be stale), and treat it as "send the user to KYC".
- Refetch `GET /kyc/status/` after login, after submitting KYC, and when the dashboard loads. There is no push when an admin approves.
"""

# ---------------------------------------------------------------------------
# Frontend guide: the profile picture (rendered on GET /kyc/status/)
# ---------------------------------------------------------------------------
AVATAR_GUIDE = """

---
### Frontend notes: the user's profile picture (`avatar_url`)

The profile photo the user uploads during KYC becomes their **profile picture**.

- `data.avatar_url` is a ready-to-use image URL: **square 256 x 256, cropped around the face**, served from the Cloudinary CDN. Put it straight into an `<img src>`. Do not add anything to it.
- It is `null` until the user has submitted KYC. Show your default avatar / initials in that case.
- It is shown while the KYC is **pending or approved**. If the submission is **rejected**, it is `null` again until the user submits a new one.
- Fetch it with `GET /kyc/status/` right after login and when the dashboard loads, keep it in your auth/user state, and refetch it after a successful `POST /kyc/submit/` (the submit response also carries `images.profile_photo_url`, the same picture).
- The URL is stable (it does not expire), so it is safe to cache in state or `localStorage`.
"""

# ---------------------------------------------------------------------------
# Frontend guide: submitting the images (rendered on POST /kyc/submit/)
# ---------------------------------------------------------------------------
IMAGES_GUIDE = """

---
### Frontend notes: profile photo + ID front/back

**`POST /kyc/submit/` is now `multipart/form-data`, not JSON.** A JSON body is rejected with `415 Unsupported Media Type`.

**Fields**

| Key | Type | Notes |
|---|---|---|
| `personal.full_name`, `personal.date_of_birth`, `personal.nationality`, `personal.sex`, `personal.phone_number` | text | same values as before |
| `address.line1`, `address.line2`, `address.city`, `address.state`, `address.postal_code`, `address.country`, `address.entry_method` | text | same values as before |
| `identification.id_type`, `identification.id_number` | text | same values as before |
| `employment.employment_status`, `employment.source_of_income`, `employment.occupation`, `employment.employer_name`, `employment.annual_income_range`, `employment.expected_annual_volume`, `employment.transaction_frequency`, `employment.typical_transaction_size` | text | same values as before |
| `consent.accuracy_declaration`, `consent.privacy_notice_accepted`, `consent.terms_accepted`, `consent.marketing_consent` | text `"true"` / `"false"` | the first three must be `"true"` |
| **`profile_photo`** | **file** | the user's face photo. Becomes their profile picture. Required |
| **`id_front`** | **file** | front of the government ID. Required |
| **`id_back`** | **file** | back of the government ID. Required (for a passport, upload the photo page again or the back cover, as your product decides) |

Nested sections are sent as **dotted keys**. Do not send a JSON string.

**Example (axios / fetch)**

```js
const fd = new FormData();
fd.append("personal.full_name", "John Doe");
fd.append("personal.date_of_birth", "1995-04-12");
fd.append("personal.nationality", "NG");
fd.append("personal.sex", "male");
fd.append("personal.phone_number", "+2348012345678");
fd.append("address.line1", "12 Allen Avenue");
fd.append("address.city", "Ikeja");
fd.append("address.state", "Lagos");
fd.append("address.postal_code", "100271");
fd.append("address.country", "NG");
fd.append("address.entry_method", "manual");
fd.append("identification.id_type", "passport");
fd.append("identification.id_number", "A12345678");
fd.append("employment.employment_status", "employed");
fd.append("employment.source_of_income", "salary");
fd.append("employment.occupation", "Software Engineer");
fd.append("employment.employer_name", "Acme Ltd");
fd.append("employment.annual_income_range", "25k_50k");
fd.append("employment.expected_annual_volume", "5k_25k");
fd.append("employment.transaction_frequency", "monthly");
fd.append("employment.typical_transaction_size", "500_2k");
fd.append("consent.accuracy_declaration", "true");
fd.append("consent.privacy_notice_accepted", "true");
fd.append("consent.terms_accepted", "true");
fd.append("consent.marketing_consent", "false");
fd.append("profile_photo", profilePhotoFile);   // File objects from <input type="file">
fd.append("id_front", idFrontFile);
fd.append("id_back", idBackFile);

await api.post("/api/kyc/submit/", fd);          // do NOT set Content-Type yourself
```

A helper that turns the old nested object into dotted keys:

```js
function toKycFormData(sections, files) {
  const fd = new FormData();
  const walk = (obj, prefix) =>
    Object.entries(obj).forEach(([k, v]) => {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v !== null && typeof v === "object") walk(v, key);
      else fd.append(key, v ?? "");
    });
  walk(sections, "");
  fd.append("profile_photo", files.profilePhoto);
  fd.append("id_front", files.idFront);
  fd.append("id_back", files.idBack);
  return fd;
}
```

**Do not set the `Content-Type` header.** The browser must add `multipart/form-data; boundary=...` itself. The shared axios instance (`components/Api.jsx`) removes its default JSON header automatically when the body is `FormData`.

**Client-side checks (save the user a wasted upload).** Read `image_max_mb` and `image_formats` from `GET /kyc/options/`: JPG, PNG, WEBP or HEIC, max 5 MB each (the server re-checks the real file content). Show a preview of each image before submit. HEIC cannot be previewed in most desktop browsers, so show a placeholder for it.

**Upload time.** Three images go to the image host during this request, so it can take a few seconds on slow connections. Show a progress/spinner state, disable the submit button, and use a generous timeout (the axios `onUploadProgress` callback works as usual).

**Errors**

| Status | `code` | Meaning |
|---|---|---|
| 400 | `validation_error` | `errors` is keyed by field. Images are keyed `profile_photo`, `id_front`, `id_back` (e.g. `{"id_front": ["The image is too large. The maximum size is 5 MB."]}`). Show each message under the matching picker |
| 409 | `kyc_already_pending` / `kyc_already_approved` | the user cannot submit again |
| 415 | `unsupported_media_type` | the request was not multipart (a JSON body was sent) |
| 429 | `throttled` | too many attempts, use `data.retry_after_seconds` |
| 502 | `upload_failed` | the image host failed. **Nothing was saved**, let the user press submit again |
| 503 | `upload_unavailable` | image uploads are temporarily unavailable on the server |

**Locked images.** The images are saved once, with the submission. There is no endpoint to replace them. After a **rejection** the user fills the form again and uploads a new set of three images. Pre-fill the text fields from `GET /kyc/prefill/` but ask for the images again.

**Privacy.** The ID front/back are stored **privately**. They are never reachable by a plain public link, and only the admin team can view them in the admin console.
"""

# ---------------------------------------------------------------------------
# Frontend guide: reading the images back (rendered on GET /kyc/submission/)
# ---------------------------------------------------------------------------
DETAIL_IMAGES_GUIDE = """

---
### Frontend notes: reading the images back

`data.images` has three URLs:

| Key | What it is | Lifetime |
|---|---|---|
| `profile_photo_url` | the profile picture, square 256 x 256 around the face | permanent, safe to cache |
| `id_front_url` | front of the ID document | **signed and temporary** |
| `id_back_url` | back of the ID document | **signed and temporary** |

- Use the ID URLs directly in an `<img src>` straight after you receive them. **Do not store or cache them**: they stop working after a while. When an image shows a broken-link state, call `GET /kyc/submission/` again to get fresh URLs.
- Any URL can be `null` for submissions that were made before the image feature existed. Show a "not provided" placeholder.
- The ID images are for display to the owner only (for example a "Your submitted documents" panel). Do not offer to download or share them.
"""

# Logical payload of the five sections. Sent as dotted multipart keys (see IMAGES_GUIDE).
SUBMIT_REQUEST_EXAMPLE = {
    "personal": {
        "full_name": "John Doe", "date_of_birth": "1995-04-12", "nationality": "NG",
        "sex": "male", "phone_number": "+2348012345678",
    },
    "address": {
        "line1": "12 Allen Avenue", "line2": "", "city": "Ikeja", "state": "Lagos",
        "postal_code": "100271", "country": "NG", "entry_method": "autocomplete",
    },
    "identification": {"id_type": "passport", "id_number": "A12345678"},
    "employment": {
        "employment_status": "employed", "source_of_income": "salary",
        "occupation": "Software Engineer", "employer_name": "Acme Ltd",
        "annual_income_range": "25k_50k", "expected_annual_volume": "5k_25k",
        "transaction_frequency": "monthly", "typical_transaction_size": "500_2k",
    },
    "consent": {
        "accuracy_declaration": True, "privacy_notice_accepted": True,
        "terms_accepted": True, "marketing_consent": False,
    },
}

# Flat multipart definition, so Swagger renders real form fields and file pickers.
SUBMIT_MULTIPART_DOC = inline_serializer(
    name="SubmitKYCMultipart",
    fields={
        "personal.full_name": serializers.CharField(max_length=150),
        "personal.date_of_birth": serializers.DateField(),
        "personal.nationality": serializers.CharField(max_length=2, help_text="ISO 3166-1 alpha-2, e.g. NG"),
        "personal.sex": serializers.ChoiceField(choices=Sex.choices),
        "personal.phone_number": serializers.CharField(help_text="International format, e.g. +2348012345678"),
        "address.line1": serializers.CharField(max_length=255),
        "address.line2": serializers.CharField(max_length=255, required=False),
        "address.city": serializers.CharField(max_length=100),
        "address.state": serializers.CharField(max_length=100, required=False),
        "address.postal_code": serializers.CharField(max_length=20, required=False),
        "address.country": serializers.CharField(max_length=2, help_text="ISO 3166-1 alpha-2, e.g. NG"),
        "address.entry_method": serializers.ChoiceField(choices=AddressEntryMethod.choices),
        "identification.id_type": serializers.ChoiceField(choices=IDType.choices),
        "identification.id_number": serializers.CharField(max_length=60),
        "employment.employment_status": serializers.ChoiceField(choices=EmploymentStatus.choices),
        "employment.source_of_income": serializers.ChoiceField(choices=SourceOfIncome.choices),
        "employment.occupation": serializers.CharField(max_length=120),
        "employment.employer_name": serializers.CharField(
            max_length=150, required=False, help_text="Required when employed or self-employed."
        ),
        "employment.annual_income_range": serializers.ChoiceField(choices=AnnualIncomeRange.choices),
        "employment.expected_annual_volume": serializers.ChoiceField(choices=ExpectedAnnualVolume.choices),
        "employment.transaction_frequency": serializers.ChoiceField(choices=TransactionFrequency.choices),
        "employment.typical_transaction_size": serializers.ChoiceField(choices=TypicalTransactionSize.choices),
        "consent.accuracy_declaration": serializers.BooleanField(help_text="Must be true."),
        "consent.privacy_notice_accepted": serializers.BooleanField(help_text="Must be true."),
        "consent.terms_accepted": serializers.BooleanField(help_text="Must be true."),
        "consent.marketing_consent": serializers.BooleanField(required=False),
        "profile_photo": serializers.FileField(help_text="Face photo. JPG, PNG, WEBP or HEIC, max 5 MB."),
        "id_front": serializers.FileField(help_text="Front of the ID document. JPG, PNG, WEBP or HEIC, max 5 MB."),
        "id_back": serializers.FileField(help_text="Back of the ID document. JPG, PNG, WEBP or HEIC, max 5 MB."),
    },
)

_RANGE = lambda *items: [{"value": v, "label": l} for v, l in items]  # noqa: E731

OPTIONS_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "sex": _RANGE(("male", "Male"), ("female", "Female")),
        "id_types": _RANGE(("passport", "Passport"), ("national_id", "National ID card"),
                           ("drivers_license", "Driver's license"),
                           ("residence_permit", "Residence permit"), ("voters_card", "Voter's card")),
        "employment_statuses": _RANGE(("employed", "Employed"), ("self_employed", "Self-employed"),
                                      ("student", "Student"), ("retired", "Retired"),
                                      ("unemployed", "Unemployed"), ("other", "Other")),
        "sources_of_income": _RANGE(("salary", "Salary"), ("business", "Business income"),
                                    ("investments", "Investments"), ("savings", "Savings"),
                                    ("inheritance", "Inheritance"), ("gift", "Gift"), ("other", "Other")),
        "income_ranges": _RANGE(("under_10k", "Under $10,000"), ("10k_25k", "$10,000 – $25,000"),
                                ("25k_50k", "$25,000 – $50,000"), ("50k_100k", "$50,000 – $100,000"),
                                ("100k_250k", "$100,000 – $250,000"), ("250k_plus", "$250,000+")),
        "annual_volume_ranges": _RANGE(("under_5k", "Under $5,000"), ("5k_25k", "$5,000 – $25,000"),
                                       ("25k_100k", "$25,000 – $100,000"),
                                       ("100k_500k", "$100,000 – $500,000"), ("500k_plus", "$500,000+")),
        "transaction_frequencies": _RANGE(("occasionally", "Occasionally"), ("monthly", "Monthly"),
                                          ("weekly", "Weekly"), ("daily", "Daily")),
        "transaction_sizes": _RANGE(("under_500", "Under $500"), ("500_2k", "$500 – $2,000"),
                                    ("2k_10k", "$2,000 – $10,000"), ("10k_50k", "$10,000 – $50,000"),
                                    ("50k_plus", "$50,000+")),
        "address_entry_methods": _RANGE(("autocomplete", "Autocomplete"), ("manual", "Manual")),
        "min_age": 18,
        "policy_version": "1.0",
        "image_max_mb": 5,
        "image_formats": ["jpg", "png", "webp", "heic"],
    },
}

_AVATAR_EXAMPLE_URL = "https://res.cloudinary.com/demo/image/upload/c_fill,g_face,h_256,w_256/solvexmarketinc/avatars/abc123"

STATUS_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "tier": 1, "kyc_status": "rejected", "can_submit": True,
        "attempts_used": 1, "attempts_remaining": 4,
        "access": {
            "can_deposit": True, "can_withdraw": False, "can_trade": False, "can_request_loan": False,
        },
        "avatar_url": None,
        "latest_submission": {
            "id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "status": "rejected", "attempt_number": 1,
            "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": "2026-10-23T09:00:00Z",
            "rejection_reason": "ID number does not match the name provided.",
        },
    },
}

STATUS_APPROVED_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "tier": 2, "kyc_status": "approved", "can_submit": False,
        "attempts_used": 1, "attempts_remaining": 4,
        "access": {
            "can_deposit": True, "can_withdraw": True, "can_trade": True, "can_request_loan": True,
        },
        "avatar_url": _AVATAR_EXAMPLE_URL,
        "latest_submission": {
            "id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "status": "approved", "attempt_number": 1,
            "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": "2026-10-23T09:00:00Z",
            "rejection_reason": None,
        },
    },
}

PREFILL_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "personal": {"full_name": "John Doe", "date_of_birth": "", "nationality": "NG",
                     "sex": "", "phone_number": "+2348012345678"},
        "address": {"line1": "", "line2": "", "city": "", "state": "", "postal_code": "",
                    "country": "NG", "entry_method": ""},
    },
}

_IMAGES_EXAMPLE = {
    "profile_photo_url": _AVATAR_EXAMPLE_URL,
    "id_front_url": "https://res.cloudinary.com/demo/image/authenticated/s--Ab12Cd34--/solvexmarketinc/kyc/1/front",
    "id_back_url": "https://res.cloudinary.com/demo/image/authenticated/s--Ef56Gh78--/solvexmarketinc/kyc/1/back",
}

SUBMIT_SUCCESS_EXAMPLE = {
    "success": True,
    "message": "KYC submitted. We'll review it and email you the outcome.",
    "data": {
        "id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "attempt_number": 1, "status": "pending",
        "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": None, "rejection_reason": None,
        "id_type": "passport", "id_number": "****5678",
        "images": _IMAGES_EXAMPLE,
    },
}

DETAIL_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "attempt_number": 1, "status": "pending",
        "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": None, "rejection_reason": None,
        "personal": SUBMIT_REQUEST_EXAMPLE["personal"],
        "address": SUBMIT_REQUEST_EXAMPLE["address"],
        "identification": {"id_type": "passport", "id_number": "****5678"},
        "images": _IMAGES_EXAMPLE,
        "employment": SUBMIT_REQUEST_EXAMPLE["employment"],
        "consent": {
            "accuracy_declaration": True, "privacy_notice_accepted": True, "terms_accepted": True,
            "marketing_consent": False, "policy_version": "1.0", "consented_at": "2026-10-22T14:30:00Z",
        },
    },
}

HISTORY_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "count": 2, "next": None, "previous": None,
        "results": [
            {"id": "9a1b2c3d-0000-4000-8000-000000000002", "attempt_number": 2, "status": "pending",
             "submitted_at": "2026-10-24T10:00:00Z", "reviewed_at": None, "rejection_reason": None},
            {"id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "attempt_number": 1, "status": "rejected",
             "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": "2026-10-23T09:00:00Z",
             "rejection_reason": "ID number does not match the name provided."},
        ],
    },
}


# --------------------------------------------------------------------------- #
# Error docs. Local helper because several KYC statuses carry more than one
# error code (and `data`), which core.openapi.error_response_doc cannot express.
# Serializer is cached so the component name is registered once.
# --------------------------------------------------------------------------- #
_ERROR_SERIALIZER = inline_serializer(
    name="KYCErrorEnvelope",
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


def kyc_error(description, *variants):
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


UNAUTHENTICATED = kyc_error(
    "Missing, invalid or expired access token.",
    {"code": "not_authenticated", "message": "Authentication credentials were not provided."},
    {"code": "token_not_valid", "message": "Given token not valid for any token type."},
)