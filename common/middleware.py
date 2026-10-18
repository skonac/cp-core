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
