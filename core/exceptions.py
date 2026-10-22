import logging
import math

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf_exc
from rest_framework.response import Response
from rest_framework.views import set_rollback
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .responses import error_payload

logger = logging.getLogger(__name__)

OUR_PERMISSION_CODES = {"kyc_required", "email_not_verified", "invalid_cron_secret", "permission_denied"}
DRF_BUILTIN_CODES = {
    "invalid", "required", "null", "blank", "max_length", "min_length", "max_value", "min_value",
    "unique", "does_not_exist", "incorrect_type", "invalid_choice", "empty", "not_a_list",
    "max_string_length", "max_digits", "max_decimal_places", "max_whole_digits", "datetime", "date",
    "overflow", "no_name", "invalid_image", "min_length", "password_mismatch",
}


class BusinessError(Exception):
    """Raise anywhere; the global handler turns it into the standard error envelope."""

    def __init__(self, code, message, status=400, data=None, errors=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.data = data
        self.errors = errors or {}


def _respond(message, code, status, errors=None, data=None):
    return Response(error_payload(message, code, errors=errors, data=data), status=status)


def _flatten(detail, parent=""):
    """Flatten DRF error detail into {field: [messages]}."""
    out = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            path = f"{parent}.{key}" if parent else str(key)
            out.update(_flatten(value, path))
    elif isinstance(detail, (list, tuple)):
        if all(isinstance(i, str) for i in detail):
            if detail:
                out[parent or "non_field_errors"] = [str(i) for i in detail]
        else:
            for idx, item in enumerate(detail):
                if isinstance(item, (dict, list, tuple)):
                    out.update(_flatten(item, f"{parent}.{idx}" if parent else str(idx)))
                else:
                    out.setdefault(parent or "non_field_errors", []).append(str(item))
    else:
        out[parent or "non_field_errors"] = [str(detail)]
    return out


def _single_business_code(exc):
    """If a serializer raised ValidationError(..., code='x') for ONE non-field case, return 'x'."""
    codes = exc.get_codes()
    if isinstance(codes, dict) and set(codes) == {"non_field_errors"}:
        codes = codes["non_field_errors"]
    if isinstance(codes, list) and len(codes) == 1 and isinstance(codes[0], str):
        if codes[0] not in DRF_BUILTIN_CODES:
            return codes[0]
    return None


def custom_exception_handler(exc, context):
    # 1. our own business errors
    if isinstance(exc, BusinessError):
        set_rollback()
        return _respond(exc.message, exc.code, exc.status, errors=exc.errors, data=exc.data)

    # 2. validation
    if isinstance(exc, drf_exc.ValidationError):
        set_rollback()
        code = _single_business_code(exc) or "validation_error"
        return _respond("Please correct the highlighted fields.", code, 400, errors=_flatten(exc.detail))

    # 3. authentication (simplejwt errors BEFORE the generic AuthenticationFailed)
    if isinstance(exc, (InvalidToken, TokenError)):
        set_rollback()
        message = "Token is invalid or expired."
        if isinstance(exc, InvalidToken) and isinstance(exc.detail, dict) and exc.detail.get("detail"):
            message = str(exc.detail["detail"])
        return _respond(message, "token_not_valid", 401)
    if isinstance(exc, (drf_exc.NotAuthenticated, drf_exc.AuthenticationFailed)):
        set_rollback()
        return _respond(str(exc.detail), "not_authenticated", 401)

    # 4. permissions
    if isinstance(exc, (drf_exc.PermissionDenied, DjangoPermissionDenied)):
        set_rollback()
        code = "permission_denied"
        if isinstance(exc, drf_exc.PermissionDenied):
            codes = exc.get_codes()
            if isinstance(codes, str) and codes in OUR_PERMISSION_CODES:
                code = codes
            message = str(exc.detail)
        else:
            message = "You do not have permission to perform this action."
        return _respond(message, code, 403)

    # 5. not found / method / throttle
    if isinstance(exc, (Http404, drf_exc.NotFound)):
        set_rollback()
        return _respond("The requested resource was not found.", "not_found", 404)
    if isinstance(exc, drf_exc.MethodNotAllowed):
        set_rollback()
        return _respond(str(exc.detail), "method_not_allowed", 405)
    if isinstance(exc, drf_exc.Throttled):
        set_rollback()
        wait = int(math.ceil(exc.wait)) if exc.wait else None
        return _respond(
            "Too many requests. Please try again later.", "throttled", 429,
            data={"retry_after_seconds": wait},
        )

    # 6. other DRF errors (bad JSON, unsupported media type, ...)
    if isinstance(exc, drf_exc.APIException) and exc.status_code < 500:
        set_rollback()
        code = exc.get_codes() if isinstance(exc.get_codes(), str) else "error"
        return _respond(str(exc.detail), code, exc.status_code)

    # 7. anything else -> 500, never leak internals
    view = context.get("view")
    logger.error("Unhandled exception in %s", view.__class__.__name__ if view else "unknown", exc_info=exc)
    return _respond("Something went wrong. Please try again later.", "server_error", 500)
