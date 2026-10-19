"""The face-worker queue lives in media_image (faceworker.md, "Job model"; design/core.md).

claim(worker_id)          -> Image or None    selfies first, then photos round-robin by event
store_result(id, body)    -> (http status, message)
"""
import logging
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from events.models import Event
from media.models import Face, Image

log = logging.getLogger("cp.queue")
S = Image.Status


def candidates():
    """Images a worker may take: waiting, or claimed by a worker whose lease ran out."""
    return (Image.objects.filter(deleted_at__isnull=True, status__in=[S.PENDING, S.PROCESSING])
            .filter(Q(status=S.PENDING) | Q(status=S.PROCESSING, processing_until__lt=timezone.now())))


def _pick():
    """Lock and return the next image, or None. Must run inside a transaction."""
    # 1. Selfies first, oldest first: a guest is waiting on the screen.
    selfie = (candidates().filter(kind=Image.Kind.SELFIE).order_by("created_at")
              .select_for_update(skip_locked=True).first())
    if selfie:
        return selfie
    # 2. Event photos, round-robin: the event served longest ago (never served first) gives its
    #    oldest photo. Events that are expired or have face processing off are skipped.
    running = (Event.objects
               .filter(status__in=[Event.Status.OPEN, Event.Status.CLOSED], is_processing_faces=True)
               .filter(Exists(candidates().filter(event=OuterRef("pk"), kind=Image.Kind.PHOTO)))
               .order_by(F("last_claimed_at").asc(nulls_first=True), "id")
               .values_list("pk", flat=True))
    for event_id in running[:5]:   # more than one only if another worker holds the first event's rows
        photo = (candidates().filter(event_id=event_id, kind=Image.Kind.PHOTO).order_by("created_at")
                 .select_for_update(skip_locked=True).first())
        if photo:
            Event.objects.filter(pk=event_id).update(last_claimed_at=timezone.now())
            return photo
    return None


def claim(worker_id: str):
    while True:
        with transaction.atomic():
            image = _pick()
            if image is None:
                return None
            if image.attempts >= settings.WORKER_MAX_ATTEMPTS:
                # Its lease ran out too often (worker crashed on it?): stop cycling, leave for review.
                image.status, image.processing_until = S.FAILED, None
                image.error = image.error or "max_attempts"
                image.save(update_fields=["status", "processing_until", "error"])
                log.warning("image %s failed after %s attempts", image.pk, image.attempts)
                continue
            image.status = S.PROCESSING
            image.processing_until = timezone.now() + timedelta(seconds=settings.WORKER_LEASE)
            image.worker = worker_id
            image.attempts += 1
            image.save(update_fields=["status", "processing_until", "worker", "attempts"])
            return image


def queue_counts() -> dict:
    c = candidates()
    return {"selfies": c.filter(kind=Image.Kind.SELFIE).count(), "photos": c.filter(kind=Image.Kind.PHOTO).count()}


# ---------------------------------------------------------------------------- results

def _number(v, lo=-math.inf, hi=math.inf) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def check_result(body: dict, is_selfie: bool) -> str | None:
    """Contract check of a success result (same rules as the worker's mockcore). None = fine."""
    if not isinstance(body.get("engine"), str) or "/" not in body["engine"]:
        return "engine must be a string like 'yunet-...+sface-.../1400/0.8'"
    faces = body.get("faces")
    if not isinstance(faces, list):
        return "faces must be a list"
    if is_selfie:
        if not isinstance(body.get("faces_found"), int) or body["faces_found"] < len(faces):
            return "main_face needs faces_found"
        if len(faces) > 1:
            return "main_face returns at most one face"
    for i, f in enumerate(faces):
        if not isinstance(f, dict):
            return f"face {i}: not an object"
        bbox = f.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(_number(v, 0, 1) for v in bbox)
                and bbox[0] < bbox[2] and bbox[1] < bbox[3]):
            return f"face {i}: bbox must be [x1, y1, x2, y2] normalized 0-1"
        lm = f.get("landmarks")
        if not (isinstance(lm, list) and len(lm) == 5
                and all(isinstance(p, list) and len(p) == 2 and all(_number(v) for v in p) for p in lm)):
            return f"face {i}: landmarks must be 5 [x, y] pairs"
        if not _number(f.get("score"), 0, 1):
            return f"face {i}: score must be 0-1"
        emb = f.get("embedding")
        if not (isinstance(emb, list) and len(emb) == 128 and all(_number(v) for v in emb)):
            return f"face {i}: embedding must be 128 numbers"
        if abs(math.sqrt(sum(v * v for v in emb)) - 1) > 1e-3:
            return f"face {i}: embedding must be unit length"
    return None


def store_result(image_id: int, body: dict) -> tuple[int, str]:
    with transaction.atomic():
        image = Image.objects.select_for_update().filter(pk=image_id, deleted_at__isnull=True).first()
        if image is None:
            return 404, "unknown or deleted image"
        if image.status == S.DONE:
            return 409, "already done"
        # Late results (lease expired, event closed meanwhile) are accepted: the work is done.

        if "error" in body:
            image.error = str(body.get("error"))[:32]
            image.error_detail = str(body.get("detail", ""))[:2000]
            final = image.error == "unreadable_image" or image.attempts >= settings.WORKER_MAX_ATTEMPTS
            image.status = S.FAILED if final else S.PENDING
            image.processing_until = None
            image.save(update_fields=["error", "error_detail", "status", "processing_until"])
            log.warning("image %s: worker error %s -> %s", image.pk, image.error, image.status)
            return 200, image.status

        problem = check_result(body, image.is_selfie)
        if problem:
            log.warning("image %s: result rejected: %s", image.pk, problem)
            return 400, problem

        faces = body["faces"]
        if image.is_selfie:
            num_faces = body["faces_found"]
            keep = faces if num_faces == 1 else []   # a rejected selfie keeps no face data
        else:
            num_faces, keep = len(faces), faces
        image.faces.all().delete()                   # re-processing replaces earlier faces
        Face.objects.bulk_create([
            Face(image=image, bbox=f["bbox"], landmarks=[v for point in f["landmarks"] for v in point],
                 score=f["score"], embedding=f["embedding"], engine=body["engine"][:100])
            for f in keep])
        image.status, image.num_faces = S.DONE, num_faces
        image.engine, image.processed_at, image.processing_until = body["engine"][:100], timezone.now(), None
        image.save(update_fields=["status", "num_faces", "engine", "processed_at", "processing_until"])
        return 200, "done"
