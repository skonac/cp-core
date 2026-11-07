"""Runs the worker.silent check (worker/queue.py) after a request, at most once per 5 minutes per process."""
from .queue import check_silent


class SilentWorkerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        check_silent()
        return response
