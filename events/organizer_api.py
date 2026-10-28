"""Organizer API (/api/organizer/…): events, cover, uploads, photos, participants, uploaders.
Buying credits: shop/api.py. The uploaders' own side: events/uploader_api.py.
For accounts with the organizer role (staff may open any event). Shapes and codes: docs/api.md §3.6.

Events are addressed by their code. Credits: one per accepted photo, charged at upload
(media.ingest); no refunds.
"""
from datetime import date as Date

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.utils import timezone
from ninja import File, Form, Router
from ninja.files import UploadedFile
from ninja.security import django_auth
from pydantic import Field

from accounts.models import UploaderAccess, User
from accounts.otp import E164, mask_phone, normalize_phone
from common.api import ApiError, CamelSchema, errors, file_doc, respond
from media.framing import served_relpath
from media.ingest import FRAME_FIELDS, IngestError, delete_frame, event_cover, ingest_cover, ingest_frame, ingest_photo
from media.matching import my_photos, selfie_states
from media.models import Image
from media.storage import file_response, soft_delete

from . import organizer_schemas as S
from .codes import normalize_code
from .models import Event, EventMember
from .participant import public_status
from .qr import DEFAULT_WIDTH, clamp_width, event_qr_svg

organizer_router = Router(tags=["organizer"], auth=django_auth)

MAX_FILES_PER_UPLOAD = 20
STATUS_OUT = {"pending": "queued", "processing": "processing", "done": "ready", "failed": "failed"}
STATUS_IN = {v: k for k, v in STATUS_OUT.items()}
INGEST_MESSAGES = {
    "unsupported_file_type": "JPEG, PNG, WebP veya HEIC fotoğraf yükle.",
    "unreadable_image": "Fotoğraf okunamadı.",
    "file_too_large": "Dosya çok büyük.",
    "file_required": "Dosya seç.",
    "duplicate_photo": "Bu fotoğraf bu etkinlikte zaten var.",
    "insufficient_credits": "Kontör yetersiz.",
    "event_expired": "Bu etkinliğin süresi sona erdi.",
    "frame_size_out_of_range": f"Çerçeve PNG'sinin uzun kenarı {settings.DISPLAY_SIDE}-{settings.MASTER_SIDE} px arasında olmalı.",
    "frame_wrong_orientation": "Çerçevenin yönü (dikey/yatay) seçilen slota uymuyor.",
}


# ---------------------------------------------------------------------------- helpers

def _organizer(request) -> User:
    user = request.user
    if not (user.is_organizer or user.is_staff):
        raise ApiError(403, "organizer_access_required", "Bu işlem için organizatör hesabı gerekli.")
    return user


def _event(request, code: str) -> Event:
    """The event, if this organizer owns it (staff: any). Anything else is 404."""
    _organizer(request)
    events = Event.objects.select_related("owner", "cover_image", "frame_landscape_image", "frame_portrait_image")
    if not request.user.is_staff:
        events = events.filter(owner=request.user)
    event = events.filter(code=normalize_code(code)).first()
    if event is None:
        raise ApiError(404, "event_not_found", "Etkinlik bulunamadı.")
    return event


def _not_expired(event: Event):
    if event.status == Event.Status.EXPIRED:
        raise ApiError(410, "event_expired", "Bu etkinliğin süresi sona erdi.")


def _photos(event):
    return Image.objects.filter(event=event, kind=Image.Kind.PHOTO, deleted_at__isnull=True)


def _stats(events) -> dict[int, dict]:
    """{event_id: photo counts by status + participants}, two queries for any number of events."""
    ids = [e.id for e in events]
    stats = {e.id: {"total": 0, "pending": 0, "processing": 0, "done": 0, "failed": 0, "participants": 0}
             for e in events}
    for event_id, status, n in (Image.objects.filter(event_id__in=ids, kind=Image.Kind.PHOTO, deleted_at__isnull=True)
                                .values_list("event_id", "status").annotate(n=Count("id"))):
        stats[event_id][status] += n
        stats[event_id]["total"] += n
    owners = {e.id: e.owner_id for e in events}
    for event_id, user_id in EventMember.objects.filter(event_id__in=ids).values_list("event_id", "user_id"):
        if user_id != owners[event_id]:   # the organizer looking at their own event is not a participant
            stats[event_id]["participants"] += 1
    return stats


def _balance(user) -> int:
    return User.objects.values_list("credit_balance", flat=True).get(pk=user.pk)


def _frame_url(event, image, orientation: str, base: str) -> str | None:
    if image is None or image.deleted_at is not None:
        return None
    return f"{base}/{event.display_code}/frame/{orientation}.png"


def event_json(request, event: Event, stats: dict, base: str = "/api/organizer/events", join_url: bool = True) -> dict:
    code = event.display_code
    uploaded = event.cover_image is not None and event.cover_image.deleted_at is None
    return {
        "id": code, "code": code, "name": event.name, "type": event.type, "date": event.date.isoformat(),
        "city": event.city or None, "venue": event.venue or None, "status": public_status(event),
        "hasFaceSearch": event.is_processing_faces, "allowsBrowseAll": event.is_browse_all,
        "requiresConsent": event.is_consent_required,
        "photoCount": stats["total"], "processedPhotoCount": stats["done"],
        "pendingPhotoCount": stats["pending"] + stats["processing"], "failedPhotoCount": stats["failed"],
        "participantCount": stats["participants"],
        "coverThumbnailUrl": f"{base}/{code}/cover/thumb.jpg" if uploaded or stats["total"] else None,
        "coverIsUploaded": uploaded,
        "frameLandscapeUrl": _frame_url(event, event.frame_landscape_image, "landscape", base),
        "framePortraitUrl": _frame_url(event, event.frame_portrait_image, "portrait", base),
        "joinUrl": request.build_absolute_uri(f"/?event={code}") if join_url else None,
        "createdAt": event.created_at.isoformat(),
    }


def _one(request, event: Event) -> dict:
    return {"event": event_json(request, event, _stats([event])[event.id])}


def _display_size(img: Image) -> tuple[int, int]:
    scale = min(1.0, settings.DISPLAY_SIDE / max(img.width or 1, img.height or 1))
    return round((img.width or 0) * scale), round((img.height or 0) * scale)


def photo_json(event: Event, img: Image, root: str = "/api/organizer/events") -> dict:
    base = f"{root}/{event.display_code}/photos/{img.seq_str}"
    width, height = _display_size(img)
    return {"id": img.seq_str, "fileName": f"{img.seq_str}.jpg", "status": STATUS_OUT[img.status],
            "faceCount": img.num_faces, "thumbnailUrl": f"{base}/thumb.jpg", "displayUrl": f"{base}/display.jpg",
            "width": width, "height": height,
            "takenAt": img.taken_at.isoformat() if img.taken_at else None,
            "uploadedAt": img.created_at.isoformat(), "errorCode": img.error or None,
            "uploadedBy": img.uploaded_by.full_name if img.uploaded_by_id else None}


def _ingest_error(e: IngestError):
    status = 410 if e.code == "event_expired" else 422
    return ApiError(status, e.code, INGEST_MESSAGES.get(e.code, e.code))


# ---------------------------------------------------------------------------- dashboard, events

@organizer_router.get("/dashboard", response={200: S.DashboardResponse, **errors(401, 403)})
def dashboard(request):
    """Balance, totals, events that are not expired (newest first).

    Errors: 403 organizer_access_required."""
    user = _organizer(request)
    events = list(Event.objects.filter(owner=user).exclude(status=Event.Status.EXPIRED)
                  .select_related("cover_image", "frame_landscape_image", "frame_portrait_image").order_by("-created_at"))
    stats = _stats(events)
    return respond(request, "organizer_dashboard_retrieved", {
        "organizer": {"displayName": user.full_name or None, "creditBalance": _balance(user)},
        "totals": {"activeEvents": sum(e.status == Event.Status.OPEN for e in events),
                   "totalPhotos": sum(s["total"] for s in stats.values()),
                   "totalParticipants": sum(s["participants"] for s in stats.values())},
        "events": [event_json(request, e, stats[e.id]) for e in events],
        "recentActivity": [],
    })


@organizer_router.get("/events", response={200: S.EventListResponse, **errors(401, 403)})
def list_events(request, includeExpired: bool = False):
    """My events, newest first; expired ones only with `includeExpired=true`.

    Errors: 403 organizer_access_required."""
    user = _organizer(request)
    events = Event.objects.filter(owner=user).select_related("cover_image", "frame_landscape_image", "frame_portrait_image").order_by("-created_at")
    if not includeExpired:
        events = events.exclude(status=Event.Status.EXPIRED)
    events = list(events)
    stats = _stats(events)
    return respond(request, "organizer_events_retrieved",
                   {"events": [event_json(request, e, stats[e.id]) for e in events], "total": len(events)})


class EventCreateIn(CamelSchema):
    name: str = Field(min_length=1, max_length=200)
    type: S.EventType = "wedding"
    date: Date
    city: str = Field("", max_length=120)
    venue: str = Field("", max_length=120)
    has_face_search: bool = True
    allows_browse_all: bool = False
    requires_consent: bool = True


def _check_rule(faces: bool, browse: bool):
    if not faces and not browse:
        raise ApiError(422, "invalid_event_settings",
                       "Yüz araması kapalı bir etkinlikte tüm fotoğraflara göz atma açık olmalı.")


@organizer_router.post("/events", response={201: S.EventCreatedResponse, **errors(401, 403, 422)})
def create_event(request, data: Form[EventCreateIn], cover: File[UploadedFile] = None):
    """multipart/form-data: name, type, date (YYYY-MM-DD), city, venue (optional), hasFaceSearch,
    allowsBrowseAll, requiresConsent (optional, defaults true/false/true), cover (optional image).
    The event is open at once; the code is generated. Creating costs nothing.

    Errors: 403 organizer_access_required · 422 validation_error, invalid_event_settings,
    unsupported_file_type, unreadable_image, file_too_large (cover)."""
    user = _organizer(request)
    _check_rule(data.has_face_search, data.allows_browse_all)
    name = data.name.strip()
    if not name:
        raise ApiError(422, "validation_error", "Etkinlik adı gerekli.")
    with transaction.atomic():
        event = Event.objects.create(
            name=name, type=data.type, date=data.date, city=data.city.strip(), venue=data.venue.strip(),
            owner=user, is_processing_faces=data.has_face_search, is_browse_all=data.allows_browse_all,
            is_consent_required=data.requires_consent)
        if cover is not None:
            try:
                ingest_cover(event, user, cover.read())
            except IngestError as e:
                transaction.set_rollback(True)
                raise _ingest_error(e) from None
    return respond(request, "organizer_event_created", _one(request, event), status=201)


@organizer_router.get("/events/{code}", response={200: S.EventResponse, **errors(401, 403, 404)})
def get_event(request, code: str):
    """One event with counts, settings and the join link.

    Errors: 403 organizer_access_required · 404 event_not_found."""
    return respond(request, "organizer_event_retrieved", _one(request, _event(request, code)))


class EventPatchIn(CamelSchema):
    name: str | None = Field(None, min_length=1, max_length=200)
    type: S.EventType | None = None
    date: Date | None = None
    city: str | None = Field(None, max_length=120)
    venue: str | None = Field(None, max_length=120)
    status: S.Literal["active", "access_closed"] | None = None
    has_face_search: bool | None = None
    allows_browse_all: bool | None = None
    requires_consent: bool | None = None


@organizer_router.patch("/events/{code}", response={200: S.EventUpdatedResponse, **errors(401, 403, 404, 410, 422)})
def update_event(request, code: str, data: EventPatchIn):
    """Change any of: name, type, date, city, venue, settings, and `status` (`active` = open,
    `access_closed` = participants locked out). Omitted fields stay.

    Errors: 403 organizer_access_required · 404 event_not_found · 410 event_expired ·
    422 validation_error, invalid_event_settings."""
    event = _event(request, code)
    _not_expired(event)
    fields = data.model_dump(exclude_unset=True)
    mapping = {"name": "name", "type": "type", "date": "date", "city": "city", "venue": "venue",
               "has_face_search": "is_processing_faces", "allows_browse_all": "is_browse_all",
               "requires_consent": "is_consent_required"}
    for key, attr in mapping.items():
        if key in fields and fields[key] is not None:
            value = fields[key].strip() if isinstance(fields[key], str) else fields[key]
            setattr(event, attr, value)
    if fields.get("status"):
        event.status = Event.Status.OPEN if fields["status"] == "active" else Event.Status.CLOSED
    if not event.name:
        raise ApiError(422, "validation_error", "Etkinlik adı gerekli.")
    _check_rule(event.is_processing_faces, event.is_browse_all)
    event.save()
    return respond(request, "organizer_event_updated", _one(request, event))


# ---------------------------------------------------------------------------- QR code

@organizer_router.get("/events/{code}/qr", response=errors(401, 403, 404),
                      openapi_extra=file_doc("image/svg+xml",
                                             "QR code (SVG) for the event's joinUrl. Content never changes."))
def event_qr(request, code: str, width: int = DEFAULT_WIDTH):
    """SVG QR code encoding the event's `joinUrl`, for printing or display. `width` (px, default
    370) is silently clamped to 111-800. Works regardless of event status (active, access_closed,
    expired) - it only answers 404 once the event itself is gone. Cached forever: regenerating from
    the same joinUrl gives the same bytes.

    Errors: 404 event_not_found."""
    event = _event(request, code)
    join_url = request.build_absolute_uri(f"/?event={event.display_code}")
    svg = event_qr_svg(join_url, clamp_width(width))
    response = HttpResponse(svg, content_type="image/svg+xml")
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


# ---------------------------------------------------------------------------- cover

@organizer_router.post("/events/{code}/cover", response={200: S.CoverResponse, **errors(401, 403, 404, 410, 422)})
def put_cover(request, code: str, file: File[UploadedFile]):
    """multipart/form-data: file (image). Free; replaces the previous uploaded cover.

    Errors: 404 event_not_found · 410 event_expired · 422 unsupported_file_type, unreadable_image, file_too_large."""
    event = _event(request, code)
    try:
        ingest_cover(event, request.user, file.read())
    except IngestError as e:
        raise _ingest_error(e) from None
    return respond(request, "organizer_cover_updated", _one(request, event))


@organizer_router.delete("/events/{code}/cover", response={200: S.CoverResponse, **errors(401, 403, 404)})
def delete_cover(request, code: str):
    """Removes the uploaded cover; the first photo is shown instead. Errors: 404 event_not_found."""
    event = _event(request, code)
    with transaction.atomic():
        for old in Image.objects.filter(event=event, kind=Image.Kind.COVER, deleted_at__isnull=True):
            soft_delete(old)
        Event.objects.filter(pk=event.pk).update(cover_image=None)
        event.cover_image = None
    return respond(request, "organizer_cover_updated", _one(request, event))


def _cover_file(request, code: str, rendition: str):
    image = event_cover(_event(request, code))
    if image is None:
        raise ApiError(404, "not_found", "Kapak yok.")
    return file_response(image.relpath(rendition))


@organizer_router.get("/events/{code}/cover/thumb.jpg", response=errors(401, 403, 404),
                      openapi_extra=file_doc("image/jpeg", "Cover ~400 px (uploaded cover, else the first photo)"))
def cover_thumb(request, code: str):
    return _cover_file(request, code, "t")


@organizer_router.get("/events/{code}/cover/display.jpg", response=errors(401, 403, 404),
                      openapi_extra=file_doc("image/jpeg", "Cover ~1600 px"))
def cover_display(request, code: str):
    return _cover_file(request, code, "d")


# ---------------------------------------------------------------------------- frame

def _orientation(value: str) -> str:
    if value not in FRAME_FIELDS:
        raise ApiError(422, "invalid_orientation", "orientation: landscape veya portrait.")
    return value


@organizer_router.post("/events/{code}/frame", response={200: S.FrameResponse, **errors(401, 403, 404, 410, 422)})
def put_frame(request, code: str, orientation: str, file: File[UploadedFile]):
    """multipart/form-data: file (PNG). `orientation`: landscape or portrait - which slot. Free;
    replaces that slot's previous frame. The PNG is kept exactly as uploaded (no re-encoding, so
    transparency survives); long side must be 1600-4000 px and match the slot's shape (a
    landscape-shaped PNG for the landscape slot, portrait-shaped for portrait; square counts as
    landscape). No check of the alpha channel or how much of it is transparent.

    Errors: 404 event_not_found · 410 event_expired · 422 invalid_orientation,
    unsupported_file_type, unreadable_image, file_too_large, frame_size_out_of_range,
    frame_wrong_orientation."""
    event = _event(request, code)
    orientation = _orientation(orientation)
    try:
        ingest_frame(event, request.user, file.read(), orientation)
    except IngestError as e:
        raise _ingest_error(e) from None
    return respond(request, "organizer_frame_updated", _one(request, event))


@organizer_router.delete("/events/{code}/frame", response={200: S.FrameResponse, **errors(401, 403, 404, 422)})
def remove_frame(request, code: str, orientation: str):
    """Removes that slot's frame, if any (not an error on an already-empty slot).

    Errors: 404 event_not_found · 422 invalid_orientation."""
    event = _event(request, code)
    delete_frame(event, _orientation(orientation))
    return respond(request, "organizer_frame_updated", _one(request, event))


@organizer_router.get("/events/{code}/frame/{orientation}.png", response=errors(401, 403, 404, 422),
                      openapi_extra=file_doc("image/png", "That slot's frame PNG, exactly as uploaded."))
def frame_file(request, code: str, orientation: str):
    event = _event(request, code)
    image = getattr(event, FRAME_FIELDS[_orientation(orientation)])
    if image is None or image.deleted_at is not None:
        raise ApiError(404, "not_found", "Çerçeve yok.")
    return file_response(image.relpath(), content_type="image/png")


# ---------------------------------------------------------------------------- uploads

class UploadCheckIn(CamelSchema):
    photo_count: int = Field(ge=0, le=100_000)


@organizer_router.post("/events/{code}/photo-upload-check", response={200: S.UploadCheckResponse, **errors(401, 403, 404, 410, 422)})
def upload_check(request, code: str, data: UploadCheckIn):
    """Before an upload: does the balance cover `photoCount` photos? Not enough is not an error
    (`eligible: false`, `missingCredits`). The upload itself checks every photo again.

    Errors: 404 event_not_found · 410 event_expired."""
    event = _event(request, code)
    _not_expired(event)
    payer = User.objects.get(pk=event.owner_id)
    allowance = payer.credit_balance - payer.effective_credit_floor
    eligible = data.photo_count <= allowance
    return respond(request, "photo_upload_eligibility_checked", {
        "eligible": eligible, "requiredCredits": data.photo_count, "creditBalance": payer.credit_balance,
        "remainingCredits": payer.credit_balance - data.photo_count if eligible else None,
        "missingCredits": 0 if eligible else data.photo_count - max(allowance, 0),
    })


@organizer_router.post("/events/{code}/photos", response={200: S.UploadBatchResponse, **errors(401, 403, 404, 410, 422)})
def upload_photos(request, code: str, files: File[list[UploadedFile]], clientIds: Form[list[str]] = None):
    """multipart/form-data: repeated `files` (max 20 per request, ≤ 50 MB each, whole request ≤ 60 MB)
    and optionally the same number of `clientIds`, echoed back in `results`. Each file is accepted or
    rejected on its own; 1 credit per accepted photo. Per-file `errorCode`: unsupported_file_type,
    unreadable_image, file_too_large, duplicate_photo, insufficient_credits.

    Errors: 404 event_not_found · 410 event_expired · 422 too_many_files, validation_error."""
    event = _event(request, code)
    _not_expired(event)
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise ApiError(422, "too_many_files", f"En fazla {MAX_FILES_PER_UPLOAD} dosya gönder.")
    ids = clientIds or [str(i) for i in range(len(files))]
    if len(ids) != len(files):
        raise ApiError(422, "validation_error", "clientIds ve files sayısı aynı olmalı.")
    results, accepted = [], 0
    for client_id, upload in zip(ids, files):
        try:
            image = ingest_photo(event, request.user, upload.read())
        except IngestError as e:
            results.append({"clientId": client_id, "photoId": None, "fileName": None,
                            "status": "rejected", "errorCode": e.code})
            continue
        accepted += 1
        results.append({"clientId": client_id, "photoId": image.seq_str, "fileName": f"{image.seq_str}.jpg",
                        "status": "queued" if event.is_processing_faces else "ready", "errorCode": None})
    return respond(request, "organizer_photos_uploaded", {
        "acceptedCount": accepted, "rejectedCount": len(files) - accepted,
        "creditBalance": _balance(event.owner), "results": results})


# ---------------------------------------------------------------------------- photos

@organizer_router.get("/events/{code}/photos", response={200: S.PhotoListResponse, **errors(401, 403, 404, 422)})
def list_photos(request, code: str, status: str = "", cursor: int = 0, limit: int = 100):
    """All photos of the event in upload order; `status` filter: queued, processing, ready, failed.
    `cursor` is an offset, `limit` ≤ 500.

    Errors: 404 event_not_found · 422 validation_error (bad status)."""
    event = _event(request, code)
    photos = _photos(event)
    by_status = dict(photos.values_list("status").annotate(n=Count("id")))
    if status:
        if status not in STATUS_IN:
            raise ApiError(422, "validation_error", "status: queued, processing, ready or failed.")
        photos = photos.filter(status=STATUS_IN[status])
    limit = max(1, min(limit, 500))
    total = photos.count()
    page = list(photos.select_related("uploaded_by").order_by("seq")[cursor:cursor + limit])
    return respond(request, "organizer_photos_retrieved", {
        "photos": [photo_json(event, p) for p in page], "total": total,
        "counts": {STATUS_OUT[s]: by_status.get(s, 0) for s in STATUS_OUT},
        "nextCursor": cursor + limit if cursor + limit < total else None,
    })


class PhotoIdsIn(CamelSchema):
    photo_ids: list[str] = Field(min_length=1, max_length=5000)


@organizer_router.delete("/events/{code}/photos", response={200: S.PhotosDeletedResponse, **errors(401, 403, 404, 422)})
def delete_photos(request, code: str, data: PhotoIdsIn):
    """JSON body {"photoIds": ["00017", ...]}: deletes those photos (files and faces at once).
    Credits are not refunded. Unknown ids are skipped (`affectedCount` tells how many went).

    Errors: 404 event_not_found."""
    event = _event(request, code)
    seqs = [int(p) for p in data.photo_ids if p.isdigit()]
    with transaction.atomic():
        images = list(_photos(event).filter(seq__in=seqs).select_for_update())
        for image in images:
            soft_delete(image)
        if event.cover_image_id in {i.id for i in images}:
            Event.objects.filter(pk=event.pk).update(cover_image=None)
    return respond(request, "organizer_photos_deleted",
                   {"affectedCount": len(images), "creditBalance": _balance(event.owner)})


def _photo_file(request, code: str, photo_id: str, rendition: str):
    event = _event(request, code)
    image = _photos(event).filter(seq=int(photo_id)).first() if photo_id.isdigit() else None
    if image is None:
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    return file_response(served_relpath(image, rendition, event))


@organizer_router.get("/events/{code}/photos/{photo_id}/thumb.jpg", response=errors(401, 403, 404),
                      openapi_extra=file_doc("image/jpeg", "~400 px"))
def photo_thumb(request, code: str, photo_id: str):
    return _photo_file(request, code, photo_id, "t")


@organizer_router.get("/events/{code}/photos/{photo_id}/display.jpg", response=errors(401, 403, 404),
                      openapi_extra=file_doc("image/jpeg", "~1600 px"))
def photo_display(request, code: str, photo_id: str):
    return _photo_file(request, code, photo_id, "d")


# ---------------------------------------------------------------------------- participants

def _match_status(user, state: str) -> str:
    if not user.is_consent:
        return "consent_pending"
    return {"no_selfie": "selfie_pending", "selfie_processing": "processing",
            "selfie_rejected": "failed", "ready": "completed"}[state]


@organizer_router.get("/events/{code}/participants", response={200: S.ParticipantListResponse, **errors(401, 403, 404)})
def participants(request, code: str):
    """People who opened the event (most recently active first): name, masked phone, where they
    are in consent → selfie → matching, and how many photos they appear in. No phone numbers,
    selfies or face data.

    Errors: 404 event_not_found."""
    event = _event(request, code)
    members = list(EventMember.objects.filter(event=event).exclude(user_id=event.owner_id)
                   .select_related("user").order_by("-last_activity_at"))
    states = selfie_states([m.user_id for m in members])
    rows, counts = [], {"completed": 0, "pending": 0, "failed": 0}
    for m in members:
        status = _match_status(m.user, states[m.user_id])
        matched = None
        if status == "completed" and event.is_processing_faces:
            matched = len(my_photos(m.user, event))
        counts[status if status in ("completed", "failed") else "pending"] += 1
        rows.append({"membershipId": str(m.pk), "displayName": m.user.full_name or None,
                     "maskedPhone": mask_phone(m.user.phone), "joinedAt": m.joined_at.isoformat(),
                     "lastActiveAt": m.last_activity_at.isoformat(), "hasConsent": m.user.is_consent,
                     "matchStatus": status, "matchedPhotoCount": matched})
    return respond(request, "organizer_participants_retrieved",
                   {"participants": rows, "total": len(rows), "counts": counts})


# ---------------------------------------------------------------------------- uploaders

def uploader_json(a: UploaderAccess) -> dict:
    events = sorted(a.events.all(), key=lambda e: (e.date, e.code))
    return {"id": str(a.pk), "fullName": a.full_name, "phoneNumber": a.phone, "status": a.status,
            "events": [{"code": e.display_code, "name": e.name, "status": public_status(e)} for e in events],
            "createdAt": a.created_at.isoformat(),
            "lastLoginAt": a.last_login_at.isoformat() if a.last_login_at else None}


def _own_uploader(request, uploader_id: str) -> UploaderAccess:
    access = (UploaderAccess.objects.filter(organizer=request.user, pk=int(uploader_id)).first()
              if uploader_id.isdigit() else None)
    if access is None:
        raise ApiError(404, "uploader_not_found", "Kayıt bulunamadı.")
    return access


def _assignable_events(user, codes: list[str]) -> list[Event]:
    """The organizer's own events for these codes (not expired). Any other code -> 422."""
    wanted = {normalize_code(c) for c in codes}
    found = list(Event.objects.filter(owner=user, code__in=wanted).exclude(status=Event.Status.EXPIRED))
    missing = wanted - {e.code for e in found}
    if missing:
        raise ApiError(422, "validation_error", "Etkinlik bulunamadı.",
                       errors={"eventCodes": [f"unknown or expired: {c.upper()}" for c in sorted(missing)]})
    return found


@organizer_router.get("/uploaders", response={200: S.UploaderListResponse, **errors(401, 403)})
def list_uploaders(request, event: str = ""):
    """Phone numbers this organizer lets upload, newest first (revoked ones too), each with its events.
    `event=RK238`: only the uploaders assigned to that event.

    Errors: 403 organizer_access_required."""
    user = _organizer(request)
    rows = UploaderAccess.objects.filter(organizer=user).prefetch_related("events").order_by("-created_at")
    if event:
        rows = rows.filter(events__code=normalize_code(event))
    rows = list(rows)
    return respond(request, "organizer_uploaders_retrieved",
                   {"uploaders": [uploader_json(a) for a in rows], "total": len(rows)})


class UploaderIn(CamelSchema):
    full_name: str = Field(min_length=1, max_length=150)
    phone_number: str
    event_codes: list[str] = Field(default_factory=list, max_length=500)


@organizer_router.post("/uploaders", response={201: S.UploaderResponse, **errors(401, 403, 409, 422)})
def add_uploader(request, data: UploaderIn):
    """Let a phone number upload to the chosen events (`eventCodes`, this organizer's, not expired), on this
    organizer's credits. No account is created for it; the uploader signs in with the organizer entry.
    One uploader per phone: to give it more events, PATCH it.

    Errors: 403 organizer_access_required · 409 uploader_already_exists (this phone is already an
    active uploader of this organizer) · 422 invalid_phone_number, validation_error (also an unknown or
    expired event code, in errors.eventCodes)."""
    user = _organizer(request)
    phone, name = normalize_phone(data.phone_number), data.full_name.strip()
    if not E164.match(phone):
        raise ApiError(422, "invalid_phone_number", "Geçerli bir cep telefonu numarası gir (+90…).")
    if not name:
        raise ApiError(422, "validation_error", "Ad gerekli.", errors={"fullName": ["required"]})
    events = _assignable_events(user, data.event_codes)
    if UploaderAccess.objects.filter(organizer=user, phone=phone, status=UploaderAccess.Status.ACTIVE).exists():
        raise ApiError(409, "uploader_already_exists", "Bu telefon numarası zaten ekli.")
    with transaction.atomic():
        access = UploaderAccess.objects.create(organizer=user, phone=phone, full_name=name)
        access.events.set(events)
    return respond(request, "organizer_uploader_created", {"uploader": uploader_json(access)}, status=201)


class UploaderPatchIn(CamelSchema):
    full_name: str | None = Field(None, min_length=1, max_length=150)
    event_codes: list[str] | None = Field(None, max_length=500)


@organizer_router.patch("/uploaders/{uploader_id}", response={200: S.UploaderUpdatedResponse, **errors(401, 403, 404, 410, 422)})
def update_uploader(request, uploader_id: str, data: UploaderPatchIn):
    """Change the name and/or the events (`eventCodes` replaces the whole set; `[]` = none). Takes effect at
    once, also in an open session. Assignments to expired events are kept as they are (they no longer count).

    Errors: 403 organizer_access_required · 404 uploader_not_found · 410 uploader_revoked (add it again
    instead) · 422 validation_error (also an unknown or expired event code)."""
    _organizer(request)
    access = _own_uploader(request, uploader_id)
    if access.status != UploaderAccess.Status.ACTIVE:
        raise ApiError(410, "uploader_revoked", "Bu kayıt kaldırılmış.")
    with transaction.atomic():
        if data.full_name is not None:
            access.full_name = data.full_name.strip()
            if not access.full_name:
                raise ApiError(422, "validation_error", "Ad gerekli.", errors={"fullName": ["required"]})
            access.save(update_fields=["full_name"])
        if data.event_codes is not None:
            expired = list(access.events.filter(status=Event.Status.EXPIRED))
            access.events.set(_assignable_events(request.user, data.event_codes) + expired)
    return respond(request, "organizer_uploader_updated", {"uploader": uploader_json(access)})


@organizer_router.delete("/uploaders/{uploader_id}", response={200: S.UploaderRevokedResponse, **errors(401, 403, 404)})
def revoke_uploader(request, uploader_id: str):
    """Revoke: the phone can no longer upload for this organizer, at once (also in an open session).
    The row stays (status `revoked`, its events kept for the record), with the photos it uploaded. Idempotent.

    Errors: 403 organizer_access_required · 404 uploader_not_found."""
    _organizer(request)
    access = _own_uploader(request, uploader_id)
    if access.status == UploaderAccess.Status.ACTIVE:
        access.status, access.revoked_at = UploaderAccess.Status.REVOKED, timezone.now()
        access.save(update_fields=["status", "revoked_at"])
    return respond(request, "organizer_uploader_revoked", {"uploader": uploader_json(access)})
