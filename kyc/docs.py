"""Reusable OpenAPI examples + a small error helper for the KYC endpoints."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

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
    },
}

STATUS_EXAMPLE = {
    "success": True,
    "message": "Success",
    "data": {
        "tier": 1, "kyc_status": "rejected", "can_submit": True,
        "attempts_used": 1, "attempts_remaining": 4,
        "latest_submission": {
            "id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "status": "rejected", "attempt_number": 1,
            "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": "2026-10-23T09:00:00Z",
            "rejection_reason": "ID number does not match the name provided.",
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

SUBMIT_SUCCESS_EXAMPLE = {
    "success": True,
    "message": "KYC submitted. We'll review it and email you the outcome.",
    "data": {
        "id": "3f6c1c0e-5b7e-4f0a-9d0b-0c6e8f7a1a11", "attempt_number": 1, "status": "pending",
        "submitted_at": "2026-10-22T14:30:00Z", "reviewed_at": None, "rejection_reason": None,
        "id_type": "passport", "id_number": "****5678",
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
