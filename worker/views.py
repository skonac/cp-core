"""Face-worker protocol endpoints (faceworker.md, "Protocol"). Plain JSON in the worker's own
shapes, not cp-web's envelope. Auth: shared bearer token, compared in constant time.

    GET  /api/faceworker/health
    GET  /api/faceworker/jobs/next?wait=30      long-poll: 200 job, 204 no work
    GET  /api/faceworker/images/<id>.jpg        display rendition
    POST /api/faceworker/jobs/<id>/result
"""
import hmac
import json
import logging
import time
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from config import __version__
from media.models import Image
from media.storage import file_response

from . import queue
from .models import Worker

log = logging.getLogger("cp.worker")


def worker_auth(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        expected = settings.WORKER_TOKEN
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return JsonResponse({"error": "unauthorized"}, status=401)
        request.worker_id = (request.headers.get("X-Worker-Id") or "unknown")[:64]
        return view(request, *args, **kwargs)
    return wrapper


def _seen(request):
    """Monitoring: last poll time and version per worker (one small row each)."""
    agent = request.headers.get("User-Agent", "")
    Worker.objects.update_or_create(worker_id=request.worker_id, defaults={
        "version": agent.removeprefix("cp-faceworker/")[:100],
        "ip": request.META.get("REMOTE_ADDR"),
        "last_seen_at": timezone.now(),
    })


@require_GET
@worker_auth
def health(request):
    _seen(request)
    return JsonResponse({"status": "ok", "app": "cp-core", "version": __version__, "queue": queue.queue_counts()})


@require_GET
@worker_auth
def next_job(request):
    _seen(request)
    try:
        wait = max(0.0, min(float(request.GET.get("wait", 0)), settings.WORKER_POLL_MAX))
    except ValueError:
        wait = 0.0
    deadline = time.monotonic() + wait
    while True:
        image = queue.claim(request.worker_id)
        if image:
            task = "main_face" if image.is_selfie else "all_faces"
            log.info("job %s (%s) -> %s", image.pk, task, request.worker_id)
            return JsonResponse({"job_id": str(image.pk), "task": task,
                                 "image_url": f"/api/faceworker/images/{image.pk}.jpg",   # relative: same host
                                 "lease_seconds": settings.WORKER_LEASE})
        if time.monotonic() >= deadline:
            return HttpResponse(status=204)
        time.sleep(1)   # one cheap indexed query per second while idle


@require_GET
@worker_auth
def image(request, image_id: int):
    img = Image.objects.filter(pk=image_id, deleted_at__isnull=True).first()
    if img is None:
        return JsonResponse({"error": "no such image"}, status=404)
    return file_response(img.relpath("d"))


@csrf_exempt   # machine-to-machine; the bearer token is the protection
@require_POST
@worker_auth
def result(request, image_id: int):
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict):
            raise ValueError("not an object")
    except ValueError as e:
        return JsonResponse({"error": f"bad json: {e}"}, status=400)
    status, message = queue.store_result(image_id, body)
    return JsonResponse({"status": message}, status=status)
