"""Uploader sessions (accounts.UploaderAccess): who the signed-in phone may upload for.

An uploader signs in with the organizer entry; if their phone has no account, no User is created
and the session only holds the verified phone (SESSION_KEY). A phone that has an account is simply
signed in, and its verified phone counts the same way. Every uploader request re-checks the
access rows, so revoking takes effect at once.
"""
from . import kvkk
from .models import UploaderAccess

SESSION_KEY = "uploader_phone"


def session_phone(request) -> str:
    """The verified phone of this session for uploader purposes, or ""."""
    phone = request.session.get(SESSION_KEY, "")
    if not phone and request.user.is_authenticated:
        phone = request.user.phone
    return phone


def active_accesses(phone: str):
    return (UploaderAccess.objects.filter(phone=phone, status=UploaderAccess.Status.ACTIVE)
            .select_related("organizer").order_by("created_at"))


def organizer_name(organizer) -> str:
    return organizer.full_name or "Organizatör"


def access_json(access: UploaderAccess) -> dict:
    return {"id": str(access.pk), "organizerName": organizer_name(access.organizer),
            "uploaderName": access.full_name}


def uploader_user_json(phone: str, accesses) -> dict:
    """The `user` object for an uploader session without an account (same shape as a user;
    `id` is empty, no roles, no consent, no selfie)."""
    first = next(iter(accesses), None)
    return {"id": "", "phoneNumber": phone, "fullName": first.full_name if first else None, "email": None,
            "profileCompleted": True, "accountRoles": [], "hasConsent": False, "consentVersion": None,
            "kvkk": kvkk.documents(), "selfieState": "no_selfie", "selfieThumbnailUrl": None}
