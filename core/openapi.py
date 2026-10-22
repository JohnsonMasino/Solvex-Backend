"""OpenAPI helpers used by EVERY app (see General Guide section 9)."""
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

# inline_serializer builds a NEW class on every call; re-using one class per component name
# prevents drf-spectacular's "identical component names" warnings.
_SERIALIZER_CACHE = {}


def _cached(key, factory):
    if key not in _SERIALIZER_CACHE:
        _SERIALIZER_CACHE[key] = factory()
    return _SERIALIZER_CACHE[key]


def success_response_doc(name, description, example=None, data_serializer=None, message="Success"):
    """Documents the standard success envelope. `example` is the full JSON body shown in Swagger."""
    def build():
        data_field = data_serializer() if data_serializer else serializers.JSONField()
        return inline_serializer(name=name, fields={
            "success": serializers.BooleanField(default=True),
            "message": serializers.CharField(default=message),
            "data": data_field,
        })

    ser = _cached(("ok", name, data_serializer), build)
    examples = [OpenApiExample(f"{name} example", value=example, response_only=True)] if example else None
    return OpenApiResponse(response=ser, description=description, examples=examples)


def error_response_doc(description, code, message, errors=None, status_hint=None):
    def build():
        return inline_serializer(name=f"Error_{code}", fields={
            "success": serializers.BooleanField(default=False),
            "message": serializers.CharField(),
            "code": serializers.CharField(),
            "errors": serializers.DictField(
                child=serializers.ListField(child=serializers.CharField()), required=False),
            "data": serializers.JSONField(allow_null=True, required=False),
        })

    ser = _cached(("err", code), build)
    example = {"success": False, "message": message, "code": code, "errors": errors or {}, "data": None}
    return OpenApiResponse(
        response=ser, description=description,
        examples=[OpenApiExample(code, value=example, response_only=True)],
    )
