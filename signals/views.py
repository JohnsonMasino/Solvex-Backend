from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from . import docs
from .serializers import EligibilityEnvelopeSerializer
from .services import is_email_eligible

ELIGIBLE_MESSAGE = "You are eligible to activate a trading signal."
NOT_ELIGIBLE_MESSAGE = "You are not eligible for a trading signal at this time."


class SignalEligibilityView(APIView):
    """GET /api/signals/eligibility/. Uses the project default: JWT + IsAuthenticated."""

    @extend_schema(
        tags=["Signals"],
        summary="Check if the logged-in user can activate a trading signal",
        description=docs.ELIGIBILITY_GUIDE,
        responses={
            200: OpenApiResponse(
                response=EligibilityEnvelopeSerializer,
                description="Always 200 for an authenticated user. Branch on `data.eligible`.",
                examples=[
                    OpenApiExample("Eligible", value=docs.ELIGIBLE_EXAMPLE, response_only=True),
                    OpenApiExample("Not eligible", value=docs.NOT_ELIGIBLE_EXAMPLE, response_only=True),
                ],
            ),
            401: docs.UNAUTHENTICATED,
        },
    )
    def get(self, request):
        eligible = is_email_eligible(request.user.email)
        response = Response(
            {
                "success": True,
                "message": "Success",
                "data": {
                    "eligible": eligible,
                    "message": ELIGIBLE_MESSAGE if eligible else NOT_ELIGIBLE_MESSAGE,
                },
            }
        )
        # Admins can change the list at any moment, so never let a browser/CDN cache this.
        response["Cache-Control"] = "no-store"
        return response