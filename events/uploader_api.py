"""Uploader API (/api/uploader/…): a phone an organizer added as uploader
(accounts.UploaderAccess) lists the organizer's events it is assigned to and uploads photos to them
on the organizer's credits. Nothing else: no event settings, participants, credits, shop or uploader management.
Shapes are the organizer API's (docs/api.md §3.10); an uploader sees and deletes only the photos
uploaded with their phone. Every request re-checks the access, so a revoke takes effect at once.

Auth: the session's verified phone (accounts.uploader.session_phone), with the CSRF check like
every other signed-in endpoint (403 csrf_failed).
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from ninja import File, Form, Router
from ninja.files import UploadedFile
from ninja.security import APIKeyCookie

from accounts.models import UploaderAccess, User
from accounts.uploader import access_json, active_accesses, session_phone
from common.api import ApiError, errors, file_doc, respond
from media.framing import served_relpath
from media.ingest import IngestError, event_cover, ingest_photo
from media.models import Image
from media.storage import file_response, soft_delete

from . import organizer_schemas as S
from .codes import normalize_code
from .models import Event
from .organizer_api import (MAX_FILES_PER_UPLOAD, STATUS_IN, STATUS_OUT, PhotoIdsIn, UploadCheckIn, _ingest_error,
                            _stats, event_json, photo_json)


class UploaderSession(APIKeyCookie):
    """The session cookie, CSRF-checked on unsafe methods; request.auth = the verified phone."""
    param_name = settings.SESSION_COOKIE_NAME

    def authenticate(self, request, key):
        return session_phone(request) or None


uploader_router = Router(tags=["uploader"], auth=UploaderSession())


# ---------------------------------------------------------------------------- helpers

def _access(request, access_id: str) -> UploaderAccess:
    access = (UploaderAccess.objects.select_related("organizer")
              .filter(pk=int(access_id), phone=request.auth, status=UploaderAccess.Status.ACTIVE).first()
              if access_id.isdigit() else None)
    if access is None:
        raise ApiError(404, "uploader_access_not_found", "Bu organizatör için yetkin yok.")
    return access


def _root(access: UploaderAccess) -> str:
    return f"/api/uploader/accesses/{access.pk}/events"


def _events(access: UploaderAccess):
    """The events this uploader is assigned to, not expired."""
    return (access.events.filter(owner=access.organizer_id).exclude(status=Event.Status.EXPIRED)
            .select_related("cover_image"))


def _event(request, access_id: str, code: str) -> tuple[UploaderAccess, Event]:
    access = _access(request, access_id)
    event = _events(access).filter(code=normalize_code(code)).first()
    if event is None:
        raise ApiError(404, "event_not_found", "Etkinlik bulunamadı.")
    return access, event


def _my_photos(access: UploaderAccess, event: Event):
    """Photos of this event uploaded with this phone for this organizer (also through an earlier access row)."""
    return Image.objects.filter(event=event, kind=Image.Kind.PHOTO, deleted_at__isnull=True,
                                uploaded_by__phone=access.phone, uploaded_by__organizer_id=access.organizer_id)


def _event_json(request, access, event, stats) -> dict:
    return event_json(request, event, stats, base=_root(access), join_url=False, frames=False)


def _balance(access) -> int:
    return User.objects.values_list("credit_balance", flat=True).get(pk=access.organizer_id)


# ---------------------------------------------------------------------------- accesses, events

@uploader_router.get("/accesses", response={200: S.UpAccessesResponse, **errors(401)})
def accesses(request):
    """Organizers this phone may upload for. Errors: 401 authentication_required."""
    return respond(request, "uploader_accesses_retrieved",
                   {"accesses": [access_json(a) for a in active_accesses(request.auth)]})


@uploader_router.get("/accesses/{access_id}/events", response={200: S.UpEventListResponse, **errors(401, 404)})
def list_events(request, access_id: str):
    """The organizer's events that are not expired, newest first (`joinUrl` is null here).

    Errors: 404 uploader_access_not_found (not this phone's, or revoked)."""
    access = _access(request, access_id)
    events = list(_events(access).order_by("-created_at"))
    stats = _stats(events)
    return respond(request, "uploader_events_retrieved",
                   {"events": [_event_json(request, access, e, stats[e.id]) for e in events], "total": len(events)})


@uploader_router.get("/accesses/{access_id}/events/{code}", response={200: S.UpEventResponse, **errors(401, 404)})
def get_event(request, access_id: str, code: str):
    """One event. Errors: 404 uploader_access_not_found, event_not_found (also when expired)."""
    access, event = _event(request, access_id, code)
    return respond(request, "uploader_event_retrieved",
                   {"event": _event_json(request, access, event, _stats([event])[event.id])})


@uploader_router.get("/accesses/{access_id}/events/{code}/cover/thumb.jpg", response=errors(401, 404),
                         openapi_extra=file_doc("image/jpeg", "Cover ~400 px (uploaded cover, else the first photo)"))
def cover_thumb(request, access_id: str, code: str):
    image = event_cover(_event(request, access_id, code)[1])
    if image is None:
        raise ApiError(404, "not_found", "Kapak yok.")
    return file_response(image.relpath("t"))


# ---------------------------------------------------------------------------- uploads

@uploader_router.post("/accesses/{access_id}/events/{code}/photo-upload-check",
                          response={200: S.UploadCheckResponse, **errors(401, 403, 404, 422)})
def upload_check(request, access_id: str, code: str, data: UploadCheckIn):
    """Does the organizer's balance cover `photoCount` photos? As in the organizer API.

    Errors: 404 uploader_access_not_found, event_not_found."""
    access, event = _event(request, access_id, code)
    payer = access.organizer
    allowance = payer.credit_balance - payer.effective_credit_floor
    eligible = data.photo_count <= allowance
    return respond(request, "photo_upload_eligibility_checked", {
        "eligible": eligible, "requiredCredits": data.photo_count, "creditBalance": payer.credit_balance,
        "remainingCredits": payer.credit_balance - data.photo_count if eligible else None,
        "missingCredits": 0 if eligible else data.photo_count - max(allowance, 0),
    })


@uploader_router.post("/accesses/{access_id}/events/{code}/photos",
                          response={200: S.UpUploadBatchResponse, **errors(401, 403, 404, 410, 422)})
def upload_photos(request, access_id: str, code: str, files: File[list[UploadedFile]], clientIds: Form[list[str]] = None):
    """As the organizer upload (max 20 files, 60 MB per request, per-file results); 1 credit per accepted
    photo from the organizer's balance. The photos belong to the organizer.

    Errors: 404 uploader_access_not_found, event_not_found · 410 event_expired · 422 too_many_files, validation_error."""
    access, event = _event(request, access_id, code)
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise ApiError(422, "too_many_files", f"En fazla {MAX_FILES_PER_UPLOAD} dosya gönder.")
    ids = clientIds or [str(i) for i in range(len(files))]
    if len(ids) != len(files):
        raise ApiError(422, "validation_error", "clientIds ve files sayısı aynı olmalı.")
    results, accepted = [], 0
    for client_id, upload in zip(ids, files):
        try:
            image = ingest_photo(event, access.organizer, upload.read(), uploaded_by=access)
        except IngestError as e:
            if e.code == "event_expired":
                raise _ingest_error(e) from None
            results.append({"clientId": client_id, "photoId": None, "fileName": None,
                            "status": "rejected", "errorCode": e.code})
            continue
        accepted += 1
        results.append({"clientId": client_id, "photoId": image.seq_str, "fileName": f"{image.seq_str}.jpg",
                        "status": "queued" if event.is_processing_faces else "ready", "errorCode": None})
    return respond(request, "uploader_photos_uploaded", {
        "acceptedCount": accepted, "rejectedCount": len(files) - accepted,
        "creditBalance": _balance(access), "results": results})


# ---------------------------------------------------------------------------- my photos

@uploader_router.get("/accesses/{access_id}/events/{code}/photos",
                         response={200: S.UpPhotoListResponse, **errors(401, 404, 422)})
def list_photos(request, access_id: str, code: str, status: str = "", cursor: int = 0, limit: int = 100):
    """The photos this phone uploaded to the event, upload order; `counts` covers those too.
    `status` filter: queued, processing, ready, failed.

    Errors: 404 uploader_access_not_found, event_not_found · 422 validation_error (bad status)."""
    access, event = _event(request, access_id, code)
    photos = _my_photos(access, event)
    by_status = dict(photos.values_list("status").annotate(n=Count("id")))
    if status:
        if status not in STATUS_IN:
            raise ApiError(422, "validation_error", "status: queued, processing, ready or failed.")
        photos = photos.filter(status=STATUS_IN[status])
    limit = max(1, min(limit, 500))
    total = photos.count()
    page = list(photos.select_related("uploaded_by").order_by("seq")[cursor:cursor + limit])
    return respond(request, "uploader_photos_retrieved", {
        "photos": [photo_json(event, p, root=_root(access)) for p in page], "total": total,
        "counts": {STATUS_OUT[s]: by_status.get(s, 0) for s in STATUS_OUT},
        "nextCursor": cursor + limit if cursor + limit < total else None,
    })


@uploader_router.delete("/accesses/{access_id}/events/{code}/photos",
                            response={200: S.UpPhotosDeletedResponse, **errors(401, 403, 404, 422)})
def delete_photos(request, access_id: str, code: str, data: PhotoIdsIn):
    """{"photoIds": [...]}: deletes those of the photos this phone uploaded; others are skipped.
    No refund. Errors: 404 uploader_access_not_found, event_not_found."""
    access, event = _event(request, access_id, code)
    seqs = [int(p) for p in data.photo_ids if p.isdigit()]
    with transaction.atomic():
        images = list(_my_photos(access, event).filter(seq__in=seqs).select_for_update(of=("self",)))
        for image in images:
            soft_delete(image)
        if event.cover_image_id in {i.id for i in images}:
            Event.objects.filter(pk=event.pk).update(cover_image=None)
    return respond(request, "uploader_photos_deleted", {"affectedCount": len(images), "creditBalance": _balance(access)})


def _photo_file(request, access_id: str, code: str, photo_id: str, rendition: str):
    access, event = _event(request, access_id, code)
    image = _my_photos(access, event).filter(seq=int(photo_id)).first() if photo_id.isdigit() else None
    if image is None:
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    return file_response(served_relpath(image, rendition, event))


@uploader_router.get("/accesses/{access_id}/events/{code}/photos/{photo_id}/thumb.jpg", response=errors(401, 404),
                         openapi_extra=file_doc("image/jpeg", "~400 px"))
def photo_thumb(request, access_id: str, code: str, photo_id: str):
    return _photo_file(request, access_id, code, photo_id, "t")


@uploader_router.get("/accesses/{access_id}/events/{code}/photos/{photo_id}/display.jpg", response=errors(401, 404),
                         openapi_extra=file_doc("image/jpeg", "~1600 px"))
def photo_display(request, access_id: str, code: str, photo_id: str):
    return _photo_file(request, access_id, code, photo_id, "d")
