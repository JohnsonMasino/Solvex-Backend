import logging

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .openapi import error_response_doc, success_response_doc
from .responses import error_payload, success_response

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """Public liveness probe used by Uptime Robot (GET and HEAD)."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    @extend_schema(
        tags=["System"], summary="Health check", auth=[],
        description="Pings the database and returns service status. Public, unthrottled; used by Uptime Robot.",
        responses={
            200: success_response_doc(
                "HealthOk", "Service and database are healthy.", message="OK",
                example={"success": True, "message": "OK",
                         "data": {"status": "ok", "database": "ok", "time": "2026-10-22T14:30:00Z"}}),
            503: error_response_doc("Database unreachable.", code="service_unavailable",
                                    message="Service unavailable"),
        },
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            logger.exception("health: database check failed")
            return Response(
                error_payload("Service unavailable", "service_unavailable",
                              data={"status": "degraded", "database": "error"}),
                status=503,
            )
        return success_response(
            {"status": "ok", "database": "ok", "time": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")},
            message="OK",
        )


# JSON handlers for URLs that never reach DRF (used when DEBUG=False; see config/urls.py)
def json_404(request, exception=None):
    return JsonResponse(error_payload("The requested resource was not found.", "not_found"), status=404)


def json_500(request):
    return JsonResponse(
        error_payload("Something went wrong. Please try again later.", "server_error"), status=500
    )
