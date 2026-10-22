"""JSON API: public event lookup, gallery (photos of me / all photos), photo detail, "not me", files.
Shapes and codes: docs/api.md. Access rules: events/participant.py (same as the Django pages)."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.http import StreamingHttpResponse
from django.utils import timezone
from ninja import Router
from ninja.security import django_auth

from common.api import ApiError, CamelSchema, errors, file_doc, respond
from common.ratelimit import allow
from media.matching import Match, my_matches, photo_match, similarity_to_user
from media.models import DownloadLog, FaceRejection, Image
from media.storage import abspath, file_response
from media.zipstream import stream_zip

from . import schemas as S
from .codes import normalize_code
from .models import Event
from .participant import Access, access_for, may_see_photo, public_status, touch_membership

public_router = Router(tags=["events"])
events_router = Router(tags=["events"], auth=django_auth)

BLOCKED = {
    "closed": (423, "event_access_closed", "Bu etkinliğe erişim kapatıldı."),
    "expired": (410, "event_expired", "Bu etkinliğin erişim süresi sona erdi."),
    "consent_required": (403, "consent_required", "Bu etkinlik için açık rıza gerekli."),
}


def _event(code: str) -> Event:
    event = Event.objects.select_related("owner").filter(code=normalize_code(code)).first()
    if event is None:
        raise ApiError(404, "event_not_found", "Etkinlik bulunamadı.")
    return event


def _member_access(request, code: str) -> tuple[Event, Access]:
    """Signed-in event access: membership first (any visit counts), then the access rules."""
    event = _event(code)
    touch_membership(request.user, event)
    access = access_for(request.user, event)
    if access.blocked:
        raise ApiError(*BLOCKED[access.blocked])
    return event, access


def _photos(event):
    return Image.objects.filter(event=event, kind=Image.Kind.PHOTO, deleted_at__isnull=True)


def _view_photos(request, event, access, view: str) -> tuple[list[Image], dict[int, Match]]:
    """The ordered photo list of a view, "mine" (face matches) or "all" (browse-all), and the user's
    matches {image_id: Match} (both views: "all" shows the frame on the user's photos too)."""
    if view not in ("mine", "all"):
        raise ApiError(422, "validation_error", "view must be 'mine' or 'all'.")
    if view == "all" and not access.can_browse:
        raise ApiError(403, "browse_all_not_allowed", "Bu etkinlikte tüm fotoğraflar açık değil.")
    matches = my_matches(request.user, event) if access.can_match and access.matching == "ready" else {}
    photos = _photos(event).order_by("taken_at", "seq")
    if view == "mine":
        photos = photos.filter(pk__in=list(matches))
    return list(photos), matches


def _display_size(img: Image) -> tuple[int, int]:
    scale = min(1.0, settings.DISPLAY_SIDE / max(img.width or 1, img.height or 1))
    return round((img.width or 0) * scale), round((img.height or 0) * scale)


def photo_json(event: Event, img: Image, match: Match | None = None) -> dict:
    base = f"/api/events/{event.code}/photos/{img.seq_str}"
    width, height = _display_size(img)
    return {"id": img.seq_str, "width": width, "height": height,
            "takenAt": img.taken_at.isoformat() if img.taken_at else None,
            "thumbnailUrl": f"{base}/thumb.jpg", "displayUrl": f"{base}/display.jpg",
            "downloadUrl": f"{base}/download",
            "matchedFace": match.json() if match else None}


def _seq(photo_id: str) -> int:
    if not photo_id.isdigit():
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    return int(photo_id)


# ---------------------------------------------------------------------------- public

@public_router.get("/events/{code}", auth=None, response={200: S.PublicEventResponse, **errors(404, 410, 423, 429)})
def public_event(request, code: str):
    """The welcome screen: no photos, no counts. With a session, also adds the event to the user's events.

    Errors: 404 event_not_found · 410 event_expired · 423 event_access_closed ·
    429 rate_limit_exceeded (30 per minute per IP)."""
    ip = request.META.get("REMOTE_ADDR", "")
    if not allow(f"event-lookup:{ip}", settings.EVENT_LOOKUP_PER_MIN, 60):
        raise ApiError(429, "rate_limit_exceeded", "Çok fazla istek. Biraz sonra tekrar dene.")
    event = _event(code)
    if request.user.is_authenticated:
        touch_membership(request.user, event)
    if event.status == Event.Status.EXPIRED:
        raise ApiError(*BLOCKED["expired"])
    if event.status == Event.Status.CLOSED:
        raise ApiError(*BLOCKED["closed"])
    return respond(request, "event_retrieved", {
        "code": event.display_code, "name": event.name, "type": event.type, "date": event.date.isoformat(),
        "locationLabel": event.location_label or None, "status": public_status(event),
        "hasFaceSearch": event.is_processing_faces, "allowsBrowseAll": event.is_browse_all,
        "requiresConsent": event.is_consent_required,
    })


# ---------------------------------------------------------------------------- gallery

@events_router.get("/{code}/gallery", response={200: S.GalleryResponse, **errors(401, 403, 404, 410, 422, 423)})
def gallery(request, code: str, view: str = "mine", cursor: int = 0, limit: int = 100):
    """Photos of me (`view=mine`, face matching) or all photos (`view=all`, when canBrowseAll).
    Ordered by capture time; `cursor` is an offset, `limit` ≤ 500. Always current: re-fetch to see new matches.

    Errors: 404 event_not_found · 410 event_expired · 423 event_access_closed · 403 consent_required, browse_all_not_allowed · 422 validation_error (bad view)."""
    event, access = _member_access(request, code)
    photos, matches = _view_photos(request, event, access, view)
    limit = max(1, min(limit, 500))
    page = photos[cursor:cursor + limit]
    all_photos = _photos(event)
    return respond(request, "gallery_retrieved", {
        "eventCode": event.display_code, "view": view,
        "matching": access.matching or None,          # state of "photos of me" (see docs)
        "canBrowseAll": access.can_browse,
        "photosTotal": all_photos.count(),
        "photosPending": all_photos.filter(status__in=["pending", "processing"]).count()
        if event.is_processing_faces else 0,
        "total": len(photos), "photos": [photo_json(event, p, matches.get(p.id)) for p in page],
        "nextCursor": cursor + limit if cursor + limit < len(photos) else None,
    })


@events_router.get("/{code}/gallery/photos/{photo_id}", response={200: S.GalleryPhotoResponse, **errors(401, 403, 404, 410, 422, 423)})
def gallery_photo(request, code: str, photo_id: str, view: str = "mine"):
    """One photo with previous/next in the same view, and downloads left today.

    Errors: 404 event_not_found · 410 event_expired · 423 event_access_closed · 403 consent_required, browse_all_not_allowed · 404 photo_not_found (not in this view)."""
    event, access = _member_access(request, code)
    photos, matches = _view_photos(request, event, access, view)
    seq = _seq(photo_id)
    index = next((i for i, p in enumerate(photos) if p.seq == seq), None)
    if index is None:
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    used = DownloadLog.objects.filter(user=request.user, image=photos[index],
                                      downloaded_at__gte=timezone.now() - timedelta(days=1)).count()
    return respond(request, "gallery_photo_retrieved", {
        "eventCode": event.display_code, "view": view, "photo": photo_json(event, photos[index], matches.get(photos[index].id)),
        "position": index + 1, "total": len(photos),
        "previousPhotoId": photos[index - 1].seq_str if index > 0 else None,
        "nextPhotoId": photos[index + 1].seq_str if index + 1 < len(photos) else None,
        "downloadsLeftToday": max(0, settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY - used),
    })


# ---------------------------------------------------------------------------- "not me"

class FaceRejectionIn(CamelSchema):
    face_id: str


def _face_of(image: Image, face_id: str):
    face = image.faces.filter(pk=int(face_id)).first() if face_id.isdigit() else None
    if face is None:
        raise ApiError(404, "face_not_found", "Yüz bulunamadı.")
    return face


def _photo_response(request, code: str, event: Event, image: Image, status: int):
    return respond(request, code, {"photo": photo_json(event, image, photo_match(request.user, image))}, status=status)


@events_router.post("/{code}/photos/{photo_id}/face-rejections",
                    response={201: S.FaceRejectionResponse, **errors(401, 403, 404, 410, 422, 423)})
def reject_face(request, code: str, photo_id: str, body: FaceRejectionIn):
    """"Not me": this face (`faceId`, usually the photo's `matchedFace.faceId`) is not the user. The face and
    faces closer to it than to the user's selfie are no longer matched to them, in every event; a photo
    stays in "mine" only if another face in it still matches. Idempotent. Returns the photo with its new
    `matchedFace` (`null`: no longer in "mine"). Undo: `DELETE …/face-rejections/{faceId}`.

    Errors: 403 consent_required (no consent: no face search) · 404 photo_not_found (not visible to the user),
    face_not_found (not a face of this photo; also on events without face search) · 410 event_expired ·
    423 event_access_closed."""
    event, access = _member_access(request, code)
    if not access.can_match:
        if access.matching == "consent_required":
            raise ApiError(*BLOCKED["consent_required"])
        raise ApiError(404, "face_not_found", "Yüz bulunamadı.")
    image = _photos(event).filter(seq=_seq(photo_id)).first()
    if image is None or not may_see_photo(request.user, event, image, access):
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    face = _face_of(image, body.face_id)
    FaceRejection.objects.get_or_create(user=request.user, face=face, defaults={
        "event": event, "similarity": similarity_to_user(request.user, face)})
    return _photo_response(request, "face_rejected", event, image, 201)


@events_router.delete("/{code}/photos/{photo_id}/face-rejections/{face_id}",
                      response={200: S.FaceRejectionRemovedResponse, **errors(401, 403, 404, 410, 423)})
def unreject_face(request, code: str, photo_id: str, face_id: str):
    """Undo a "not me". Returns the photo with its `matchedFace` (it may still be `null`: below the threshold,
    or covered by another rejection).

    Errors: 404 photo_not_found, face_not_found (no "not me" of this user on this face) · 410 event_expired ·
    423 event_access_closed · 403 consent_required."""
    event, _ = _member_access(request, code)
    image = _photos(event).filter(seq=_seq(photo_id)).first()
    if image is None:
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    face = _face_of(image, face_id)
    if not FaceRejection.objects.filter(user=request.user, face=face).delete()[0]:
        raise ApiError(404, "face_not_found", "Yüz bulunamadı.")
    return _photo_response(request, "face_rejection_removed", event, image, 200)


# ---------------------------------------------------------------------------- files

def _visible_photo(request, code: str, photo_id: str) -> tuple[Event, Image]:
    event, access = _member_access(request, code)
    image = _photos(event).filter(seq=_seq(photo_id)).first()
    if image is None or not may_see_photo(request.user, event, image, access):
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")   # never reveal that it exists
    return event, image


@events_router.get("/{code}/photos/{photo_id}/thumb.jpg", response=errors(401, 403, 404, 410, 423),
                   openapi_extra=file_doc("image/jpeg", "~400 px, for grids"))
def photo_thumb(request, code: str, photo_id: str):
    return file_response(_visible_photo(request, code, photo_id)[1].relpath("t"))


@events_router.get("/{code}/photos/{photo_id}/display.jpg", response=errors(401, 403, 404, 410, 423),
                   openapi_extra=file_doc("image/jpeg", "~1600 px, for viewing"))
def photo_display(request, code: str, photo_id: str):
    return file_response(_visible_photo(request, code, photo_id)[1].relpath("d"))


@events_router.get("/{code}/photos/{photo_id}/download", response=errors(401, 403, 404, 410, 423, 429),
                   openapi_extra=file_doc("image/jpeg", "Full size (≤ 4000 px), Content-Disposition: attachment"))
def photo_download(request, code: str, photo_id: str):
    """Errors: 404 photo_not_found · 429 download_limit_reached (3 per photo per 24 h)."""
    event, image = _visible_photo(request, code, photo_id)
    since = timezone.now() - timedelta(days=1)
    if DownloadLog.objects.filter(user=request.user, image=image, downloaded_at__gte=since).count() \
            >= settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY:
        raise ApiError(429, "download_limit_reached", "Bu fotoğrafı bugün yeterince indirdin. Yarın tekrar dene.")
    DownloadLog.objects.create(user=request.user, image=image, ip=request.META.get("REMOTE_ADDR"))
    return file_response(image.relpath("m"),
                         download_name=settings.DOWNLOAD_NAME.format(code=event.code, seq=image.seq))


@events_router.get("/{code}/download.zip", response=errors(401, 403, 404, 410, 422, 423, 429),
                   openapi_extra=file_doc("application/zip", "The selected photos, full size, as one ZIP "
                                                             "(stored, streamed). Headers X-Photos-Included, X-Photos-Skipped"))
def download_zip(request, code: str, view: str = "mine", photos: str = "", all: bool = False):
    """Several photos at once, full size, as a ZIP. `photos` = comma-separated photo ids of the current
    `view` (e.g. `00012,00017`), or `all=true` for the whole view. Use it as a plain link (`<a href>`):
    the browser streams it to disk. Max `CP_ZIP_MAX_FILES` photos per request (50); split larger selections.
    Every photo counts against the 3-downloads-per-day cap; photos outside the view or already at the cap
    are left out (X-Photos-Skipped tells how many).

    Errors: 403 consent_required, browse_all_not_allowed · 404 photo_not_found (none of them visible) ·
    410 event_expired · 422 validation_error (no photos given), too_many_files · 423 event_access_closed ·
    429 download_limit_reached (all of them at the cap)."""
    event, access = _member_access(request, code)
    in_view, _ = _view_photos(request, event, access, view)
    if all:
        requested, chosen = len(in_view), in_view
    else:
        ids = {int(p) for p in photos.split(",") if p.strip().isdigit()}
        if not ids:
            raise ApiError(422, "validation_error", "photos: comma-separated photo ids, or all=true.")
        requested, chosen = len(ids), [p for p in in_view if p.seq in ids]
    if len(chosen) > settings.ZIP_MAX_FILES:
        raise ApiError(422, "too_many_files", f"En fazla {settings.ZIP_MAX_FILES} fotoğraf seç; daha fazlasını parça parça indir.")
    if not chosen:
        raise ApiError(404, "photo_not_found", "Fotoğraf bulunamadı.")
    since = timezone.now() - timedelta(days=1)
    used = dict(DownloadLog.objects.filter(user=request.user, image__in=chosen, downloaded_at__gte=since)
                .values_list("image_id").annotate(n=Count("id")))
    allowed = [p for p in chosen if used.get(p.id, 0) < settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY
               and abspath(p.relpath("m")).is_file()]
    if not allowed:
        raise ApiError(429, "download_limit_reached", "Bu fotoğrafları bugün yeterince indirdin. Yarın tekrar dene.")
    ip = request.META.get("REMOTE_ADDR")
    DownloadLog.objects.bulk_create([DownloadLog(user=request.user, image=p, ip=ip) for p in allowed])
    entries = [(settings.DOWNLOAD_NAME.format(code=event.code, seq=p.seq), abspath(p.relpath("m"))) for p in allowed]
    response = StreamingHttpResponse(stream_zip(entries), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{settings.ZIP_NAME.format(code=event.code, view=view)}"'
    response["X-Photos-Included"] = str(len(allowed))
    response["X-Photos-Skipped"] = str(requested - len(allowed))
    return response
