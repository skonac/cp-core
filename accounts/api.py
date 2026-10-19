"""JSON API: sign-in, session, the signed-in user (profile, consent, selfies, events).
Shapes and codes: docs/api.md."""
from django.conf import settings
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.middleware.csrf import get_token
from ninja import File, Form, Router
from ninja.files import UploadedFile
from ninja.security import django_auth

from common.api import ApiError, CamelSchema, errors, file_doc, respond
from events.codes import normalize_code
from events.models import Event, EventMember
from events.participant import access_for, public_status
from media.ingest import IngestError, ingest_selfie
from media.matching import my_photos, selfie_state
from media.models import Image
from media.storage import file_response, soft_delete

from . import kvkk
from . import schemas as S
from .models import User
from .otp import mask_phone, request_code, verify_code

auth_router = Router(tags=["auth"])
me_router = Router(tags=["me"], auth=django_auth)
kvkk_router = Router(tags=["kvkk"])


# ---------------------------------------------------------------------------- shapes

def selfie_json(img: Image) -> dict:
    if img.status in (Image.Status.PENDING, Image.Status.PROCESSING):
        status, reason = "processing", None
    elif img.status == Image.Status.DONE and img.num_faces == 1:
        status, reason = "ready", None
    elif img.status == Image.Status.DONE:
        status, reason = "rejected", "no_face" if img.num_faces == 0 else "multiple_faces"
    else:
        status, reason = "rejected", "unreadable"
    return {"id": str(img.pk), "slot": img.slot, "status": status, "reason": reason,
            "thumbnailUrl": f"/api/me/selfies/{img.pk}/thumb.jpg", "createdAt": img.created_at.isoformat()}


def user_json(user: User) -> dict:
    selfies = Image.objects.filter(owner=user, is_selfie=True, deleted_at__isnull=True).order_by("slot")
    first = selfies.first()   # "primary" sorts before "secondary"
    return {
        "id": str(user.pk), "phoneNumber": user.phone, "fullName": user.full_name or None, "email": user.email,
        "profileCompleted": bool(user.full_name), "accountRoles": user.account_roles,
        "hasConsent": user.is_consent, "consentVersion": user.kvkk_version or None, "kvkk": kvkk.documents(),
        "selfieState": selfie_state(user),
        "selfieThumbnailUrl": f"/api/me/selfies/{first.pk}/thumb.jpg" if first else None,
    }


def next_step(user: User, event) -> str:
    if event is None:
        return "organizer_home" if user.is_organizer else "account_home"
    if not user.is_consent and (event.is_consent_required or event.is_processing_faces):
        return "consent"
    if event.is_processing_faces and selfie_state(user) in ("no_selfie", "selfie_rejected"):
        return "selfie"
    return "gallery"


# ---------------------------------------------------------------------------- auth

class OtpRequestIn(CamelSchema):
    phone_number: str
    event_code: str | None = None
    role: str = "participant"


class OtpVerifyIn(CamelSchema):
    challenge_id: str
    code: str


@auth_router.get("/csrf", auth=None, response=S.CsrfResponse)
def csrf(request):
    """Sets the csrftoken cookie. Call once on app start; send its value as X-CSRFToken."""
    get_token(request)
    return respond(request, "csrf_set")


@auth_router.post("/otp-requests", auth=None, response={201: S.OtpSentResponse, **errors(404, 422, 429)})
def otp_request(request, body: OtpRequestIn):
    """Sends a sign-in code (dev: to the server log and the admin). `role` defaults to participant.

    Errors: 404 event_not_found · 422 invalid_phone_number, invalid_role, validation_error ·
    429 rate_limit_exceeded (3 codes per phone per 15 min; 30 s between codes)."""
    event = None
    if body.event_code:
        event = Event.objects.filter(code=normalize_code(body.event_code)).first()
        if event is None:
            raise ApiError(404, "event_not_found", "Etkinlik bulunamadı.")
    challenge = request_code(body.phone_number, body.role, event, request.META.get("REMOTE_ADDR"))
    return respond(request, "otp_sent", {
        "challengeId": challenge.pk, "maskedPhone": mask_phone(challenge.phone),
        "expiresInSeconds": settings.OTP_TTL, "resendAfterSeconds": settings.OTP_RESEND_AFTER,
    }, status=201, message="Doğrulama kodu gönderildi.")


@auth_router.post("/otp-verifications", auth=None, response={200: S.SignInResponse, **errors(410, 422, 429)})
def otp_verify(request, body: OtpVerifyIn):
    """Checks the code; signs in, or creates the account. Sets the session cookie. Follow `nextStep`.

    Errors: 410 otp_expired · 422 invalid_otp, validation_error · 429 rate_limit_exceeded (5 wrong codes)."""
    user, is_new, event = verify_code(body.challenge_id, body.code)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")   # sets sessionid, rotates csrftoken
    return respond(request, "phone_verified", {
        "user": user_json(user), "nextStep": next_step(user, event), "isNewUser": is_new,
        "eventCode": event.display_code if event else None,
        "eventMembership": {"eventCode": event.display_code, "role": "participant"} if event else None,
    }, message="Telefon numarası doğrulandı.")


@auth_router.delete("/session", auth=None, response={204: None})
def sign_out(request):
    """Signs out."""
    logout(request)
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------- kvkk

@kvkk_router.api_operation(["GET", "HEAD"], "/{filename}", auth=None, response=errors(404),
                           openapi_extra=file_doc("application/pdf", "The PDF"))
def kvkk_document(request, filename: str):
    """A KVKK document PDF, current or older (what earlier consents refer to). Public.
    The current files: `user.kvkk.urls`. Errors: 404 not_found."""
    path = settings.KVKK_DIR / filename
    if not kvkk.FILENAME_RE.match(filename) or not path.is_file():
        raise ApiError(404, "not_found", "Belge bulunamadı.")
    return FileResponse(path.open("rb"), content_type="application/pdf", filename=filename)


# ---------------------------------------------------------------------------- me

class MeUpdateIn(CamelSchema):
    full_name: str
    email: str | None = None


class ConsentIn(CamelSchema):
    granted: bool
    version: str          # the KVKK version the user was shown (user.kvkk.version)
    locale: str = "tr"    # the language shown: tr | en


@me_router.get("", response={200: S.ProfileResponse, **errors(401)})
def me(request):
    """The signed-in user. 401 authentication_required when signed out (use on app start)."""
    return respond(request, "profile_retrieved", user_json(request.user))


@me_router.patch("", response={200: S.ProfileUpdatedResponse, **errors(401, 403, 409, 422)})
def me_update(request, body: MeUpdateIn):
    """Profile: fullName (required), email (optional, null clears).

    Errors: 403 csrf_failed · 409 email_already_in_use · 422 validation_error."""
    user = request.user
    name = body.full_name.strip()
    if not name or len(name) > 150:
        raise ApiError(422, "validation_error", "Ad soyad gerekli (en fazla 150 karakter).")
    email = (body.email or "").strip().lower() or None
    if email:
        try:
            validate_email(email)
        except ValidationError:
            raise ApiError(422, "validation_error", "Geçerli bir e-posta adresi gir.") from None
        if User.objects.filter(email=email).exclude(pk=user.pk).exists():
            raise ApiError(409, "email_already_in_use", "Bu e-posta başka bir hesapta kayıtlı.")
    if email != user.email:
        user.email_verified_at = None
    user.full_name, user.email = name, email
    user.save(update_fields=["full_name", "email", "email_verified_at"])
    return respond(request, "profile_updated", user_json(user), message="Profil güncellendi.")


@me_router.post("/consent", response={201: S.ConsentResponse, **errors(401, 403, 409, 422)})
def give_consent(request, body: ConsentIn):
    """KVKK explicit consent to the version the screen showed (`user.kvkk.version`); logged with
    version, language, file hash, IP and browser.

    Errors: 403 csrf_failed · 409 consent_version_outdated (re-fetch /api/me, show the new text) ·
    422 explicit_consent_required (granted false), validation_error."""
    if not body.granted:
        raise ApiError(422, "explicit_consent_required", "Açık rıza olmadan devam edilemez.")
    if body.locale not in settings.KVKK_LOCALES:
        raise ApiError(422, "validation_error", "Geçersiz dil.")
    if body.version != settings.KVKK_VERSION:
        raise ApiError(409, "consent_version_outdated", "Metin güncellendi; lütfen güncel metni oku.")
    kvkk.record_consent(request.user, body.locale, request.META.get("REMOTE_ADDR"),
                        request.META.get("HTTP_USER_AGENT", ""))
    return respond(request, "consent_recorded", {"hasConsent": True, "version": settings.KVKK_VERSION},
                   status=201, message="Açık rıza kaydedildi.")


@me_router.get("/events", response={200: S.MyEventsResponse, **errors(401)})
def my_events(request):
    """Events the user has opened, newest activity first."""
    events = []
    for m in EventMember.objects.filter(user=request.user).select_related("event").order_by("-last_activity_at"):
        e, access = m.event, access_for(request.user, m.event)
        matched = len(my_photos(request.user, e)) if access.can_match and access.matching == "ready" else 0
        events.append({
            "code": e.display_code, "name": e.name, "type": e.type, "date": e.date.isoformat(),
            "locationLabel": e.location_label or None, "status": public_status(e),
            "matchedPhotoCount": matched, "matching": access.matching or None,
            "coverThumbnailUrl": None, "lastActivityAt": m.last_activity_at.isoformat(),
            "membershipRole": "participant",
        })
    return respond(request, "user_events_retrieved", {"events": events, "total": len(events)})


@me_router.get("/selfies", response={200: S.SelfiesResponse, **errors(401)})
def selfies(request):
    """The user's selfies (slots primary, secondary). Poll after an upload until status leaves processing."""
    rows = Image.objects.filter(owner=request.user, is_selfie=True, deleted_at__isnull=True).order_by("slot")
    return respond(request, "selfies_retrieved", {"selfies": [selfie_json(s) for s in rows],
                                                  "selfieState": selfie_state(request.user)})


@me_router.post("/selfies", response={201: S.SelfieAcceptedResponse, **errors(401, 403, 422)})
def upload_selfie(request, file: File[UploadedFile], slot: Form[str] = "primary"):
    """multipart/form-data: file (JPEG/PNG/WebP/HEIC, ≤ 50 MB), slot. Replaces the slot's selfie.
    Processing is asynchronous: poll GET /api/me/selfies.

    Errors: 403 consent_required, csrf_failed · 422 unsupported_file_type, unreadable_image,
    file_too_large, invalid_slot, validation_error."""
    try:
        image = ingest_selfie(request.user, file.read(), slot)
    except IngestError as e:
        status = 403 if e.code == "consent_required" else 422
        raise ApiError(status, e.code, {
            "consent_required": "Önce açık rıza vermelisin.",
            "unsupported_file_type": "JPEG, PNG, WebP veya HEIC fotoğraf yükle.",
            "unreadable_image": "Fotoğraf okunamadı.",
            "file_too_large": "Dosya çok büyük.",
        }.get(e.code, e.code)) from None
    return respond(request, "selfie_accepted", selfie_json(image), status=201)


@me_router.delete("/selfies/{selfie_id}", response={204: None, **errors(401, 403, 404)})
def delete_selfie(request, selfie_id: int):
    """Deletes the selfie and its face data. Errors: 404 selfie_not_found."""
    image = Image.objects.filter(pk=selfie_id, owner=request.user, is_selfie=True, deleted_at__isnull=True).first()
    if image is None:
        raise ApiError(404, "selfie_not_found", "Selfie bulunamadı.")
    with transaction.atomic():
        soft_delete(image)
    return HttpResponse(status=204)


@me_router.get("/selfies/{selfie_id}/thumb.jpg", response=errors(401, 404),
               openapi_extra=file_doc("image/jpeg", "Thumbnail (owner only)"))
def selfie_thumb(request, selfie_id: int):
    """Use as <img src>. Errors: 404 selfie_not_found."""
    image = Image.objects.filter(pk=selfie_id, owner=request.user, is_selfie=True, deleted_at__isnull=True).first()
    if image is None:
        raise ApiError(404, "selfie_not_found", "Selfie bulunamadı.")
    return file_response(image.relpath("t"))
