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
