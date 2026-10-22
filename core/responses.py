from rest_framework.response import Response


def success_response(data=None, message="Success", status=200):
    return Response(
        {"success": True, "message": message, "data": data if data is not None else {}},
        status=status,
    )


def error_payload(message, code, errors=None, data=None):
    return {"success": False, "message": message, "code": code, "errors": errors or {}, "data": data}


def error_response(message, code, status=400, errors=None, data=None):
    return Response(error_payload(message, code, errors=errors, data=data), status=status)
