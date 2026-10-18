"""cp-web's response envelope, for every endpoint and every error.

    success: {"success": true,  "code": "event_retrieved", "message": "", "data": {...}, "requestId": "req_..."}
    error:   {"success": false, "code": "event_not_found", "message": "...", "data": null, "requestId": "req_..."}

Endpoints return respond(...) and raise ApiError(...); the handlers below turn everything
else (404, validation, crashes) into the same shape. JSON field names are camelCase.
"""
import logging

from django.http import Http404, JsonResponse
from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, HttpError, ValidationError as SchemaValidationError
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel

log = logging.getLogger("cp.api")


class CamelSchema(Schema):
    """Request bodies: snake_case in Python, camelCase on the wire (phone_number <-> phoneNumber)."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ApiError(Exception):
    """Raise anywhere below an endpoint: ApiError(404, "event_not_found", "Etkinlik bulunamadı.")"""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message


def _request_id(request) -> str:
    return getattr(request, "request_id", "")


def ok(request, code: str, data=None, message: str = "") -> dict:
    return {"success": True, "code": code, "message": message, "data": data, "requestId": _request_id(request)}


def respond(request, code: str, data=None, status: int = 200, message: str = "") -> JsonResponse:
    return JsonResponse(ok(request, code, data, message), status=status, json_dumps_params={"ensure_ascii": False})


def make_api(**kwargs) -> NinjaAPI:
    api = NinjaAPI(**kwargs)

    def error(request, status: int, code: str, message: str, **extra):
        body = {"success": False, "code": code, "message": message, "data": None, "requestId": _request_id(request)}
        body.update(extra)
        return JsonResponse(body, status=status, json_dumps_params={"ensure_ascii": False})

    @api.exception_handler(ApiError)
    def on_api_error(request, exc: ApiError):
        return error(request, exc.status, exc.code, exc.message)

    @api.exception_handler(Http404)
    def on_not_found(request, exc):
        return error(request, 404, "not_found", "Not found.")

    @api.exception_handler(AuthenticationError)
    def on_auth(request, exc):
        return error(request, 401, "authentication_required", "Sign in required.")

    @api.exception_handler(HttpError)
    def on_http_error(request, exc: HttpError):
        # Ninja's own errors; the one that matters: a POST/PATCH/DELETE without X-CSRFToken.
        message = str(exc)
        code = "csrf_failed" if "CSRF" in message else {400: "bad_request", 403: "forbidden", 404: "not_found"}.get(exc.status_code, "error")
        return error(request, exc.status_code, code, message)

    @api.exception_handler(SchemaValidationError)
    def on_validation(request, exc: SchemaValidationError):
        # Field errors go in an extra "errors" key: {"phoneNumber": ["..."]}; data stays null.
        errors: dict[str, list[str]] = {}
        for e in exc.errors:
            field = str(e["loc"][-1]) if e.get("loc") else "__all__"
            errors.setdefault(field, []).append(e["msg"])
        return error(request, 422, "validation_error", "Invalid input.", errors=errors)

    @api.exception_handler(Exception)
    def on_crash(request, exc):
        log.exception("unhandled error %s", _request_id(request))
        return error(request, 500, "internal_error", "Internal error.")

    return api
