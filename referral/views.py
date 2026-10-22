from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from core.exceptions import BusinessError
from core.openapi import success_response_doc
from core.pagination import StandardPagination
from core.responses import success_response
from core.throttles import AuthSensitiveThrottle
from referral import docs, services
from referral.utils import mask_name
from referral.serializers import (
    EarningItemSerializer,
    EarningsDataSerializer,
    ReferralDashboardSerializer,
    ReferralItemSerializer,
    ReferralListDataSerializer,
    ReferralListQuerySerializer,
    ValidateCodeDataSerializer,
    ValidateCodeQuerySerializer,
)

PAGE_PARAMS = [
    OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, description="Page number (default 1)."),
    OpenApiParameter("page_size", OpenApiTypes.INT, OpenApiParameter.QUERY, description="Items per page (default 20, max 100)."),
]


class ReferralDashboardView(APIView):
    @extend_schema(
        tags=["Referral"],
        summary="My referral dashboard",
        description="Returns the user's referral code, shareable link, funnel stats, earnings and the 5 most recent referrals.",
        responses={
            200: success_response_doc(
                "ReferralDashboardSuccess", "Dashboard loaded (zeros if the user has no referrals).",
                example=docs.DASHBOARD_EXAMPLE, data_serializer=ReferralDashboardSerializer,
                message="Referral dashboard loaded.",
            ),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        data = ReferralDashboardSerializer(services.get_dashboard(request.user)).data
        return success_response(data, "Referral dashboard loaded.")


class ReferralListView(APIView):
    @extend_schema(
        tags=["Referral"],
        summary="List referred users",
        description="Paginated list of people the user referred, optionally filtered by funnel stage. Names and emails are masked.",
        parameters=[
            OpenApiParameter("stage", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                             enum=list(services.STAGES), description="Filter by funnel stage."),
            *PAGE_PARAMS,
        ],
        responses={
            200: success_response_doc(
                "ReferralListSuccess", "Paginated referrals.", example=docs.LIST_EXAMPLE,
                data_serializer=ReferralListDataSerializer, message="Referrals loaded.",
            ),
            400: docs.error_doc(
                "Invalid stage filter.", "validation_error", "Please correct the highlighted fields.",
                errors={"stage": ['"abc" is not a valid choice.']},
            ),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        query = ReferralListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        items = services.list_referrals(request.user, stage=query.validated_data.get("stage"))

        paginator = StandardPagination()
        page = paginator.paginate_queryset(items, request, view=self)
        data = ReferralItemSerializer(page, many=True).data
        response = paginator.get_paginated_response(data)
        response.data["message"] = "Referrals loaded."
        return response


class ReferralEarningsView(APIView):
    @extend_schema(
        tags=["Referral"],
        summary="Referral bonus history",
        description="Paginated list of rewarded referrals (newest first) plus the lifetime total earned.",
        parameters=PAGE_PARAMS,
        responses={
            200: success_response_doc(
                "ReferralEarningsSuccess", "Paginated bonus history.", example=docs.EARNINGS_EXAMPLE,
                data_serializer=EarningsDataSerializer, message="Referral earnings loaded.",
            ),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        paginator = StandardPagination()
        page = paginator.paginate_queryset(services.list_earnings(request.user), request, view=self)
        data = EarningItemSerializer(page, many=True).data
        response = paginator.get_paginated_response(data)
        response.data["message"] = "Referral earnings loaded."
        response.data["data"]["total_earned"] = str(services.total_earned(request.user))
        return response


class ValidateReferralCodeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthSensitiveThrottle]

    @extend_schema(
        tags=["Referral"],
        summary="Validate a referral code",
        description="Public check used by the signup page before submitting the register form.",
        auth=[],
        parameters=[
            OpenApiParameter("code", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True,
                             description="Referral code (case-insensitive).", examples=[
                                 OpenApiExample("Example", value="A8K2QZ7M")]),
        ],
        responses={
            200: success_response_doc(
                "ReferralCodeValid", "Code is valid.", example=docs.VALIDATE_OK_EXAMPLE,
                data_serializer=ValidateCodeDataSerializer, message="Referral code is valid.",
            ),
            400: docs.error_doc(
                "Code missing, blank or malformed.", "validation_error", "Please correct the highlighted fields.",
                errors={"code": ["This field is required."]},
            ),
            404: docs.error_doc(
                "Unknown referral code.", "invalid_referral_code", "This referral code is not valid.",
                data={"valid": False},
            ),
            429: docs.error_doc(
                "Too many requests.", "throttled", "Request was throttled. Try again later.",
                data={"retry_after_seconds": 42},
            ),
        },
    )
    def get(self, request):
        query = ValidateCodeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        referrer = services.validate_code(query.validated_data["code"])
        if referrer is None:
            raise BusinessError(
                code="invalid_referral_code",
                message="This referral code is not valid.",
                status=404,
                data={"valid": False},
            )
        return success_response(
            {"valid": True, "referrer_name": mask_name(referrer.full_name)},
            "Referral code is valid.",
        )
