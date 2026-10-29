from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from core.openapi import error_response_doc, success_response_doc
from core.responses import success_response

from . import services
from .serializers import ProfilePhotoSerializer, ProfileSerializer, ProfileUpdateSerializer

TAG = "Profile"

_AVATAR_EXAMPLE_URL = (
    "https://res.cloudinary.com/demo/image/upload/c_fill,g_face,h_256,w_256/solvexmarketinc/avatars/abc123"
)

PROFILE_EXAMPLE = {
    "email": "john@example.com",
    "full_name": "John Doe",
    "avatar_url": _AVATAR_EXAMPLE_URL,
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

PHOTO_GUIDE = """

---
### Frontend notes: the profile picture

The profile picture lives **only** in the profile. KYC does not ask for it (KYC only collects the ID front and back).

**Showing it.** `GET /profile/` returns `data.avatar_url`: a ready-to-use image URL, **square 256 x 256 and cropped around the face**, served from the Cloudinary CDN. Put it straight into an `<img src>`. It is `null` when the user has no picture yet, so show a default avatar or the user's initials.

- Read `avatar_url` right after login and when the dashboard loads, keep it in your auth/user state, and use it in the header, sidebar and profile page.
- The URL is permanent (it does not expire), so it is safe to cache in state or `localStorage`.
- A new picture gets a **new URL**. After an upload, replace the stored `avatar_url` with the one in the response so the old picture is not shown from the browser cache.

**Changing it.** Send `POST /profile/photo/` as **`multipart/form-data`** with one file field named **`profile_photo`**. The same call adds the first picture or replaces an existing one. It is allowed at any time and is **not** affected by the KYC identity lock.

```js
const fd = new FormData();
fd.append("profile_photo", file);                 // File from <input type="file" accept="image/*,.heic,.heif">
const res = await api.post("/api/profile/photo/", fd);   // do NOT set Content-Type yourself
setAvatarUrl(res.data.data.avatar_url);
```

The response is the full profile (same shape as `GET /profile/`) with the new `avatar_url`.

**Removing it.** `DELETE /profile/photo/` removes the picture and returns the profile with `avatar_url: null`. Calling it when there is no picture is safe.

**Limits.** JPG, PNG, WEBP or HEIC, max 5 MB (`image_max_mb` and `image_formats` in `GET /kyc/options/` show the same limits). The server checks the real file content. Check the size and type in the browser first to save a wasted upload, show a preview before sending, and show a placeholder for HEIC (most desktop browsers cannot display it).

**Errors**

| Status | `code` | Meaning |
|---|---|---|
| 400 | `validation_error` | `errors.profile_photo` holds the message (no file, empty file, wrong type, too large). Show it under the picker |
| 415 | `unsupported_media_type` | the request was not multipart (a JSON body was sent) |
| 429 | `throttled` | too many uploads, use `data.retry_after_seconds` |
| 502 | `upload_failed` | the image host failed. Nothing changed, the old picture is kept. Let the user try again |
| 503 | `upload_unavailable` | image uploads are temporarily unavailable on the server |
"""


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
        description="Returns the account and profile data of the logged-in user, including `avatar_url` "
                    "(the profile picture, or `null`). An empty profile is created defensively if missing."
                    + PHOTO_GUIDE,
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
                    "itself partial. Returns 'No changes were made.' (200) when nothing differs. PUT is not supported. "
                    "This endpoint is JSON only: change the profile picture with `POST /profile/photo/`.",
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


class ProfilePhotoView(APIView):
    """POST /api/profile/photo/ (add or replace) and DELETE /api/profile/photo/ (remove)."""
    permission_classes = [IsAuthenticated]
    # The project default is JSON only; this view alone accepts file uploads.
    parser_classes = [MultiPartParser]
    # Limits uploads per user (scope "image_upload" lives in REST_FRAMEWORK in settings.py).
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "image_upload"
    http_method_names = ["post", "delete", "head", "options"]

    @extend_schema(
        tags=[TAG], summary="Upload or replace my profile picture",
        description=(
            "Adds the user's profile picture, or replaces the current one. Send **`multipart/form-data`** with a "
            "single file field named **`profile_photo`** (JPG, PNG, WEBP or HEIC, max 5 MB). The file type is "
            "checked on the server from the real file content. The picture is stored publicly and delivered as a "
            "square 256 x 256 crop around the face.\n\n"
            "Allowed at any time, with or without KYC, and not affected by the KYC identity lock. The previous "
            "picture is deleted only after the new one is saved, so a failed upload never removes the old picture. "
            "Returns the full profile with the new `avatar_url`."
            + PHOTO_GUIDE
        ),
        request={"multipart/form-data": ProfilePhotoSerializer},
        responses={
            200: success_response_doc("ProfilePhotoSuccess", "Picture saved; returns the updated profile.",
                                      data_serializer=ProfileSerializer, message="Profile picture updated.",
                                      example={"success": True, "message": "Profile picture updated.",
                                               "data": PROFILE_EXAMPLE}),
            400: error_response_doc("No file, empty file, not a real image, or too large.", code="validation_error",
                                    message="Please correct the highlighted fields.",
                                    errors={"profile_photo": ["The image is too large. The maximum size is 5 MB."]}),
            401: _unauth(),
            415: error_response_doc("The request was not sent as multipart/form-data.",
                                    code="unsupported_media_type",
                                    message='Unsupported media type "application/json" in request.'),
            429: _throttled(),
            502: error_response_doc("The image host rejected or failed the upload.", code="upload_failed",
                                    message="We couldn't upload your image. Please try again."),
            503: error_response_doc("Image uploads are not configured on the server.", code="upload_unavailable",
                                    message="Image uploads are temporarily unavailable. Please try again later."),
        },
    )
    def post(self, request):
        serializer = ProfilePhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = services.set_profile_photo(request.user, serializer.validated_data["profile_photo"])
        return success_response(ProfileSerializer(profile).data, message="Profile picture updated.")

    @extend_schema(
        tags=[TAG], summary="Remove my profile picture",
        description=(
            "Removes the user's profile picture. `avatar_url` becomes `null` in the returned profile. "
            "Safe to call when there is no picture. No request body."
        ),
        request=None,
        responses={
            200: success_response_doc("ProfilePhotoRemoveSuccess", "Picture removed; returns the updated profile.",
                                      data_serializer=ProfileSerializer, message="Profile picture removed.",
                                      example={"success": True, "message": "Profile picture removed.",
                                               "data": {**PROFILE_EXAMPLE, "avatar_url": None}}),
            401: _unauth(),
            429: _throttled(),
        },
    )
    def delete(self, request):
        profile = services.remove_profile_photo(request.user)
        return success_response(ProfileSerializer(profile).data, message="Profile picture removed.")