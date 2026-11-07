"""Signs out guests whose time is up (accounts/guest.py). After AuthenticationMiddleware."""
from .guest import sign_out_if_expired


class GuestExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        sign_out_if_expired(request)
        return self.get_response(request)
