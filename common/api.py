"""cp-web's response envelope, for every endpoint and every error.

    success: {"success": true,  "code": "event_retrieved", "message": "", "data": {...}, "requestId": "req_..."}
    error:   {"success": false, "code": "event_not_found", "message": "...", "data": null, "requestId": "req_..."}

Endpoints return respond(...) and raise ApiError(...); the handlers below turn everything
else (404, validation, crashes) into the same shape. JSON field names are camelCase.
"""
import logging
from typing import Any, Literal

from django.http import Http404, JsonResponse
from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, HttpError, ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.alias_generators import to_camel

log = logging.getLogger("cp.api")


class CamelSchema(Schema):
    """Request bodies: snake_case in Python, camelCase on the wire (phone_number <-> phoneNumber)."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Out(BaseModel):
    """Response shapes, for the docs (/api/docs) and the contract test (common/tests.py).
    Endpoints still build their JSON by hand and return respond(...); Ninja passes that through
    untouched. Field names are the wire names (camelCase), since nothing converts them.
    extra="forbid": the contract test fails on any field the docs don't list."""
    model_config = ConfigDict(extra="forbid")


REQUEST_ID = Field(description="Also in the X-Request-ID header; quote it in bug reports.",
                   examples=["req_3dc994887c3ed300"])


def envelope(name: str, data: Any, code: str) -> type[Out]:
    """The success envelope around one data shape: envelope("UserResponse", UserOut, "profile_retrieved")."""
    return create_model(name, __base__=Out,
                        success=(Literal[True], True),
                        code=(Literal[code], code),   # type: ignore[valid-type]
                        message=(str, Field("", description="Turkish text; never toast it on success.")),
                        data=(data, ...),
                        requestId=(str, REQUEST_ID))


class ErrorOut(Out):
    """Every error. `code` is the stable reason (docs/api.md §2); the endpoint description lists its codes."""
    success: Literal[False] = False
    code: str = Field(examples=["event_not_found"])
    message: str = Field(description="Turkish, safe to show.", examples=["Etkinlik bulunamadı."])
    data: None = None
    requestId: str = REQUEST_ID
    errors: dict[str, list[str]] | None = Field(None, description="422 validation_error only: field -> messages.",
                                                examples=[None])


# One model per status, so each status in /api/docs shows a typical code of its own.
_ERROR_EXAMPLES = {
    400: ("bad_request", "Invalid JSON.", None),
    401: ("authentication_required", "Sign in required.", None),
    403: ("consent_required", "Bu etkinlik için açık rıza gerekli.", None),
    404: ("not_found", "Bulunamadı.", None),
    409: ("consent_version_outdated", "Metin güncellendi; lütfen güncel metni oku.", None),
    410: ("event_expired", "Bu etkinliğin erişim süresi sona erdi.", None),
    422: ("validation_error", "Invalid input.", {"phoneNumber": ["Field required"]}),
    423: ("event_access_closed", "Bu etkinliğe erişim kapatıldı.", None),
    429: ("rate_limit_exceeded", "Çok fazla istek. Biraz sonra tekrar dene.", None),
    501: ("payment_not_available", "Ödeme henüz kullanılamıyor.", None),
    502: ("payment_provider_error", "Ödeme başlatılamadı.", None),
}
_ERROR_MODELS = {
    status: create_model(f"Error{status}", __base__=ErrorOut,
                         code=(str, Field(examples=[code])),
                         message=(str, Field(description="Turkish, safe to show.", examples=[message])),
                         errors=(dict[str, list[str]] | None,
                                 Field(None, description="422 validation_error only: field -> messages.",
                                       examples=[errs])))
    for status, (code, message, errs) in _ERROR_EXAMPLES.items()
}


def errors(*statuses: int) -> dict:
    """response={200: X, **errors(401, 404)}"""
    return {s: _ERROR_MODELS[s] for s in statuses}


def file_doc(content_type: str, description: str) -> dict:
    """openapi_extra for endpoints that send a file instead of JSON."""
    return {"responses": {200: {"description": description,
                                "content": {content_type: {"schema": {"type": "string", "format": "binary"}}}}}}


class ApiError(Exception):
    """Raise anywhere below an endpoint: ApiError(404, "event_not_found", "Etkinlik bulunamadı.")"""

    def __init__(self, status: int, code: str, message: str = "", errors: dict | None = None):
        super().__init__(message or code)
        self.status, self.code, self.message, self.errors = status, code, message, errors


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
        if exc.errors:   # field errors, same shape as 422 validation_error
            return error(request, exc.status, exc.code, exc.message, errors=exc.errors)
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
