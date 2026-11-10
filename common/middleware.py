"""Request ids: every request gets one, every response carries it (X-Request-ID header and
the envelope's requestId), and log lines can quote it. Support asks: "what was your requestId?"."""
import secrets


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = "req_" + secrets.token_hex(8)
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


class RealIpMiddleware:
    """Behind nginx every request comes from 127.0.0.1; nginx passes the client in X-Real-IP.
    Trusted only when the request really comes from the local proxy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        real = request.META.get("HTTP_X_REAL_IP")
        if real and request.META.get("REMOTE_ADDR") in ("127.0.0.1", "::1"):
            request.META["REMOTE_ADDR"] = real.strip()
        return self.get_response(request)


class OperationModeMiddleware:
    """Applies the operation mode (common/mode.py). After AuthenticationMiddleware: staff pass."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import mode as m
        current = m.current()
        if current == m.Mode.NORMAL or request.path.startswith(m.ALWAYS_OPEN):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_active and user.is_staff:
            return self.get_response(request)
        if current == m.Mode.LOCKDOWN:
            return _blocked(request, "site_locked", "Site geçici olarak kapalı. Biraz sonra tekrar dene.")
        if (request.method, request.path.rstrip("/")) == m.PAYMENT_START:
            return _blocked(request, "payments_disabled", "Ödemeler geçici olarak kapalı. Biraz sonra tekrar dene.")
        return self.get_response(request)


def _blocked(request, code: str, message: str):
    """503: the API envelope for /api/, a plain page for the Django pages."""
    from django.http import HttpResponse, JsonResponse
    if request.path.startswith("/api/"):
        body = {"success": False, "code": code, "message": message, "data": None,
                "requestId": getattr(request, "request_id", "")}
        response = JsonResponse(body, status=503, json_dumps_params={"ensure_ascii": False})
    else:
        response = HttpResponse(f"<!doctype html><meta charset=utf-8><title>Cleverphotos</title>"
                                f"<p style='font:16px sans-serif;margin:40px'>{message}</p>", status=503)
    response["Retry-After"] = "300"
    return response
