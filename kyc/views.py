from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.exceptions import BusinessError
from core.openapi import success_response_doc
from core.permissions import IsEmailVerified
from core.responses import success_response
from core.throttles import AuthSensitiveThrottle

from . import docs, services
from .models import KYCSubmission
from .serializers import (
    SubmissionCreatedSerializer, SubmissionDetailSerializer,
    SubmissionSummarySerializer, SubmitKYCSerializer,
)

TAG = "KYC"


class OptionsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG],
        summary="KYC form options",
        description="Returns every dropdown list needed to render the KYC form, each as a list of value/label pairs.",
        responses={
            200: success_response_doc("KYCOptionsSuccess", "Form options returned.", example=docs.OPTIONS_EXAMPLE),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        return success_response(services.get_form_options())


class StatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG],
        summary="My KYC status",
        description="Returns the caller's tier, KYC status, attempt counters and latest submission summary.",
        responses={
            200: success_response_doc("KYCStatusSuccess", "Current KYC status.", example=docs.STATUS_EXAMPLE),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        summary = services.get_status_summary(request.user)
        latest = summary.pop("latest")
        summary["latest_submission"] = SubmissionSummarySerializer(latest).data if latest else None
        return success_response(summary)


class PrefillView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG],
        summary="KYC prefill data",
        description="Returns known profile data using the same keys as the submit request so the form can be pre-populated.",
        responses={
            200: success_response_doc("KYCPrefillSuccess", "Prefill data (empty strings where unknown).", example=docs.PREFILL_EXAMPLE),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        return success_response(services.get_prefill_data(request.user))


class SubmitView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=[TAG],
        summary="Submit KYC",
        description="Submits all five KYC sections in one request for manual review. Tier 1 users only.",
        request=SubmitKYCSerializer,
        examples=[OpenApiExample("Valid request", value=docs.SUBMIT_REQUEST_EXAMPLE, request_only=True)],
        responses={
            201: success_response_doc(
                "KYCSubmitSuccess", "KYC submitted and queued for review.",
                example=docs.SUBMIT_SUCCESS_EXAMPLE,
                message="KYC submitted. We'll review it and email you the outcome.",
            ),
            400: docs.kyc_error(
                "One or more fields are invalid. Errors are keyed by dotted path.",
                {
                    "code": "validation_error",
                    "message": "Please correct the highlighted fields.",
                    "errors": {
                        "personal.date_of_birth": ["You must be at least 18 years old."],
                        "consent.accuracy_declaration": ["You must confirm that the information provided is accurate."],
                    },
                },
            ),
            401: docs.UNAUTHENTICATED,
            403: docs.kyc_error(
                "Email not verified, or the attempt limit was reached.",
                {"code": "email_not_verified", "message": "Please verify your email address to continue."},
                {
                    "code": "kyc_attempts_exceeded",
                    "message": "You have reached the maximum number of verification attempts. Please contact support.",
                    "data": {"attempts_used": 5, "attempts_remaining": 0},
                },
            ),
            409: docs.kyc_error(
                "State or duplicate-ID conflict.",
                {"code": "kyc_already_pending", "message": "Your verification is already under review.",
                 "data": {"kyc_status": "pending"}},
                {"code": "kyc_already_approved", "message": "Your identity is already verified.",
                 "data": {"kyc_status": "approved"}},
                {"code": "id_already_used",
                 "message": "This identification document can't be used. Please check the details or contact support."},
            ),
            429: docs.kyc_error(
                "Too many requests.",
                {"code": "throttled", "message": "Request was throttled.", "data": {"retry_after_seconds": 42}},
            ),
        },
    )
    def post(self, request):
        serializer = SubmitKYCSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission = services.submit_kyc(request.user, serializer.validated_data, request)
        return success_response(
            SubmissionCreatedSerializer(submission).data,
            message="KYC submitted. We'll review it and email you the outcome.",
            status=201,
        )


class SubmissionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG],
        summary="My latest submission",
        description="Read-only view of the caller's latest submission with the ID number masked.",
        responses={
            200: success_response_doc("KYCSubmissionSuccess", "Latest submission.", example=docs.DETAIL_EXAMPLE),
            401: docs.UNAUTHENTICATED,
            404: docs.kyc_error(
                "No submission exists yet.",
                {"code": "not_found", "message": "You have not submitted KYC yet."},
            ),
        },
    )
    def get(self, request):
        latest = (
            KYCSubmission.objects.filter(user=request.user)
            .order_by("-attempt_number", "-created_at")
            .first()
        )
        if latest is None:
            raise BusinessError("not_found", "You have not submitted KYC yet.", status=404)
        return success_response(SubmissionDetailSerializer(latest).data)


class HistoryView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SubmissionSummarySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return KYCSubmission.objects.none()
        return KYCSubmission.objects.filter(user=self.request.user).order_by("-attempt_number", "-created_at")

    @extend_schema(
        tags=[TAG],
        summary="My KYC history",
        description="Paginated list of the caller's submissions, newest first.",
        parameters=[
            OpenApiParameter("page", int, description="Page number (default 1)."),
            OpenApiParameter("page_size", int, description="Items per page (default 20, max 100)."),
        ],
        responses={
            200: success_response_doc("KYCHistorySuccess", "Paginated submissions.", example=docs.HISTORY_EXAMPLE),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
