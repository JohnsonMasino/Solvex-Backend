from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.openapi import error_response_doc, success_response_doc
from core.responses import success_response

from . import services
from .serializers import ProfileSerializer, ProfileUpdateSerializer

TAG = "Profile"

PROFILE_EXAMPLE = {
    "email": "john@example.com",
    "full_name": "John Doe",
    "date_of_birth": "1995-04-12",
    "sex": "male",
    "nationality": "NG",
    "nationality_name": "Nigeria",
    "phone_number": "+2348012345678",
    "address": {
        "line1": "12 Allen Avenue", "line2": "", "city": "Ikeja", "state": "Lagos",
        "postal_code": "100271", "country": "NG", "country_name": "Nigeria", "entry_method": "manual",
    },
    "tier": 2,
    "kyc_status": "approved",
    "identity_locked": True,
    "locked_fields": ["full_name", "date_of_birth", "nationality", "sex"],
    "is_complete": True,
    "member_since": "2026-10-22T14:30:00Z",
    "updated_at": "2026-10-22T15:00:00Z",
}

LOCKED_MESSAGE = services.LOCKED_MESSAGE


def _unauth():
    return error_response_doc("Missing or invalid access token.", code="not_authenticated",
                              message="Authentication credentials were not provided.")


def _throttled():
    return error_response_doc("Too many requests.", code="throttled",
                              message="Too many requests. Please try again later.")


class ProfileView(APIView):
    """GET /api/profile/ and PATCH /api/profile/ (PUT is not supported)."""
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    @extend_schema(
        tags=[TAG], summary="Get my profile",
        description="Returns the account and profile data of the logged-in user. "
                    "An empty profile is created defensively if missing.",
        responses={
            200: success_response_doc("ProfileSuccess", "Profile returned.",
                                      data_serializer=ProfileSerializer,
                                      example={"success": True, "message": "Success", "data": PROFILE_EXAMPLE}),
            401: _unauth(),
            429: _throttled(),
        },
    )
    def get(self, request):
        profile = services.get_or_create_profile(request.user)
        return success_response(ProfileSerializer(profile).data)

    @extend_schema(
        tags=[TAG], summary="Update my profile",
        description="Updates one, several or all editable fields. Omitted fields are untouched; `address` is "
                    "itself partial. Returns 'No changes were made.' (200) when nothing differs. PUT is not supported.",
        request=ProfileUpdateSerializer,
        examples=[
            OpenApiExample("Update phone only", request_only=True, value={"phone_number": "+2348012345678"}),
            OpenApiExample("Update everything", request_only=True, value={
                "full_name": "John A. Doe", "date_of_birth": "1995-04-12", "sex": "male", "nationality": "NG",
                "phone_number": "+2348012345678",
                "address": {"line1": "12 Allen Avenue", "line2": "", "city": "Ikeja", "state": "Lagos",
                            "postal_code": "100271", "country": "NG", "entry_method": "manual"}}),
        ],
        responses={
            200: success_response_doc("ProfileUpdateSuccess", "Profile updated (same shape as GET).",
                                      data_serializer=ProfileSerializer, message="Profile updated successfully.",
                                      example={"success": True, "message": "Profile updated successfully.",
                                               "data": PROFILE_EXAMPLE}),
            400: error_response_doc("Bad format, underage, invalid country, read-only field sent, empty body "
                                    "or incomplete address.", code="validation_error",
                                    message="Please correct the highlighted fields.",
                                    errors={"date_of_birth": ["You must be at least 18 years old."]}),
            401: _unauth(),
            403: error_response_doc("Identity field changed after KYC approval.", code="field_locked",
                                    message=LOCKED_MESSAGE,
                                    errors={"date_of_birth": ["This field is locked after identity verification."]}),
            405: error_response_doc("PUT is not supported; use PATCH.", code="method_not_allowed",
                                    message='Method "PUT" not allowed.'),
            429: _throttled(),
        },
    )
    def patch(self, request):
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile, changed = services.update_profile(request.user, serializer.validated_data)
        message = "Profile updated successfully." if changed else "No changes were made."
        return success_response(ProfileSerializer(profile).data, message=message)
