"""Reusable OpenAPI examples + uniquely named error docs for the referral app."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers


def error_doc(description, code, message, errors=None, data=None):
    """Error envelope doc with a component name unique to this app (…_Referral)."""
    ser = inline_serializer(
        name=f"Error_{code}_Referral",
        fields={
            "success": serializers.BooleanField(default=False),
            "message": serializers.CharField(),
            "code": serializers.CharField(),
            "errors": serializers.DictField(child=serializers.ListField(child=serializers.CharField()), required=False),
            "data": serializers.JSONField(allow_null=True, required=False),
        },
    )
    example = {"success": False, "message": message, "code": code, "errors": errors or {}, "data": data}
    return OpenApiResponse(
        response=ser,
        description=description,
        examples=[OpenApiExample(f"{code}", value=example, response_only=True)],
    )


UNAUTHENTICATED = error_doc(
    "Missing or invalid access token.",
    "not_authenticated",
    "Authentication credentials were not provided.",
)

RECENT_ITEM = {
    "masked_name": "John D.",
    "masked_email": "j***@gmail.com",
    "stage": "rewarded",
    "joined_at": "2026-10-22T14:30:00Z",
    "rewarded_at": "2026-10-25T09:10:00Z",
    "bonus_amount": "3.00",
}

LIST_ITEM = {
    "id": "5b0e3d8e-6f2a-4a53-9c63-0d6f1f7c9a11",
    "masked_name": "John D.",
    "masked_email": "j***@gmail.com",
    "stage": "funded",
    "status": "pending",
    "joined_at": "2026-10-22T14:30:00Z",
    "verified_at": "2026-10-22T14:35:00Z",
    "rewarded_at": None,
    "bonus_amount": None,
}

DASHBOARD_EXAMPLE = {
    "success": True,
    "message": "Referral dashboard loaded.",
    "data": {
        "code": "A8K2QZ7M",
        "link": "https://app.solvexmarket.com/signup?ref=A8K2QZ7M",
        "bonus_per_referral": "3.00",
        "currency": "USD",
        "stats": {
            "total_referred": 7, "verified": 6, "funded": 4, "rewarded": 3,
            "pending": 4, "total_earned": "9.00", "potential_earnings": "12.00",
        },
        "recent": [RECENT_ITEM],
    },
}

LIST_EXAMPLE = {
    "success": True,
    "message": "Referrals loaded.",
    "data": {"count": 1, "next": None, "previous": None, "results": [LIST_ITEM]},
}

EARNINGS_EXAMPLE = {
    "success": True,
    "message": "Referral earnings loaded.",
    "data": {
        "count": 1, "next": None, "previous": None, "total_earned": "3.00",
        "results": [{
            "id": "5b0e3d8e-6f2a-4a53-9c63-0d6f1f7c9a11",
            "masked_name": "John D.",
            "bonus_amount": "3.00",
            "rewarded_at": "2026-10-25T09:10:00Z",
        }],
    },
}

VALIDATE_OK_EXAMPLE = {
    "success": True,
    "message": "Referral code is valid.",
    "data": {"valid": True, "referrer_name": "John D."},
}
